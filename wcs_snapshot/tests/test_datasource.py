import codecs
import datetime
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET

import pytest
import responses

from wcs import data_sources, fields
from wcs.categories import DataSourceCategory
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.form import Form, get_request
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import WorkflowStatusItem

from .test_widgets import MockHtmlForm, mock_form_submission
from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
[wscall-secrets]
api.example.com = 1234
'''
        )

    pub.load_site_options()

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def no_request_pub(pub, request):
    pub._request = None


@pytest.fixture
def requests_pub(pub, request):
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    pub._set_request(req)
    return req


@pytest.fixture
def error_email(pub):
    pub.cfg['debug'] = {'error_email': 'errors@localhost.invalid'}
    pub.write_cfg()
    pub.set_config()


def test_item_field_jsonvalue_datasource(requests_pub):
    req = get_request()
    req.environ['REQUEST_METHOD'] = 'POST'
    field = fields.ItemField()
    field.id = 1
    field.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': 1, 'text': 'un'}, {'id': 2, 'text': 'deux'}]),
    }
    form = Form()
    field.add_to_form(form)
    widget = form.get_widget('f1')
    assert widget is not None
    assert widget.options == [('1', 'un', '1'), ('2', 'deux', '2')]

    form = MockHtmlForm(widget)
    mock_form_submission(req, widget, {'f1': ['1']})
    assert widget.parse() == '1'

    form = Form()
    field.add_to_view_form(form, value='1')
    widget = form.get_widget('f1')

    form = MockHtmlForm(widget)
    mock_form_submission(req, widget)
    assert widget.parse() == '1'


def test_jsonvalue_datasource(pub):
    plain_list = [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list)}
    assert data_sources.get_items(datasource) == [
        ('1', 'foo', '1', {'id': '1', 'text': 'foo'}),
        ('2', 'bar', '2', {'id': '2', 'text': 'bar'}),
    ]
    assert data_sources.get_structured_items(datasource) == [
        {'id': '1', 'text': 'foo'},
        {'id': '2', 'text': 'bar'},
    ]

    # with key
    plain_list = [{'id': '1', 'text': 'foo', 'key': 'a'}, {'id': '2', 'text': 'bar', 'key': 'b'}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list)}
    assert data_sources.get_items(datasource) == [
        ('1', 'foo', 'a', {'id': '1', 'key': 'a', 'text': 'foo'}),
        ('2', 'bar', 'b', {'id': '2', 'key': 'b', 'text': 'bar'}),
    ]


def test_jsonvalue_datasource_errors(pub):
    # not a list
    datasource = {'type': 'jsonvalue', 'value': 'foobar', 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert logged_error.summary == "Data source: JSON data source ('foobar') gave a non usable result"

    LoggedError.wipe()
    plain_list = {'foo': 'bar'}
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('{\"foo\": \"bar\"}') gave a non usable result"
    )

    # not a list of dict
    LoggedError.wipe()
    plain_list = ['foobar']
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert logged_error.summary == "Data source: JSON data source ('[\"foobar\"]') gave a non usable result"

    LoggedError.wipe()
    plain_list = [{'foo': 'bar'}, 'foobar']
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('[{\"foo\": \"bar\"}, \"foobar\"]') gave a non usable result"
    )

    # no id found
    LoggedError.wipe()
    plain_list = [{'text': 'foo'}, {'id': '2', 'text': 'bar'}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('[{\"text\": \"foo\"}, {\"id\": \"2\", \"text\": \"bar\"}]') gave a non usable result"
    )

    LoggedError.wipe()
    plain_list = [{'id': '1', 'text': 'foo'}, {'id': '', 'text': 'bar'}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('[{\"id\": \"1\", \"text\": \"foo\"}, {\"id\": \"\", \"text\": \"bar\"}]') gave a non usable result"
    )

    # no text found
    LoggedError.wipe()
    plain_list = [{'id': '1'}, {'id': '2', 'text': 'bar'}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('[{\"id\": \"1\"}, {\"id\": \"2\", \"text\": \"bar\"}]') gave a non usable result"
    )

    LoggedError.wipe()
    plain_list = [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': ''}]
    datasource = {'type': 'jsonvalue', 'value': json.dumps(plain_list), 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == "Data source: JSON data source ('[{\"id\": \"1\", \"text\": \"foo\"}, {\"id\": \"2\", \"text\": \"\"}]') gave a non usable result"
    )

    LoggedError.wipe()
    # value not configured
    datasource = {'type': 'jsonvalue', 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Data source: JSON data source (None) gave a non usable result'


def test_jsonvalue_datasource_with_template(pub):
    template = """
    {% if form_var_foo %}
    [{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}]
    {% else %}
    [{"id": "3", "text": "baz"}, {"id": "4", "text": "bam"}]
    {% endif %}
    """
    datasource = {'type': 'jsonvalue', 'value': template}
    assert data_sources.get_items(datasource) == [
        ('3', 'baz', '3', {'id': '3', 'text': 'baz'}),
        ('4', 'bam', '4', {'id': '4', 'text': 'bam'}),
    ]
    assert data_sources.get_structured_items(datasource) == [
        {'id': '3', 'text': 'baz'},
        {'id': '4', 'text': 'bam'},
    ]

    class FormVarFoo:
        def get_substitution_variables(self):
            return {'form_var_foo': 'xxx'}

    pub.substitutions.feed(FormVarFoo())
    assert data_sources.get_items(datasource) == [
        ('1', 'foo', '1', {'id': '1', 'text': 'foo'}),
        ('2', 'bar', '2', {'id': '2', 'text': 'bar'}),
    ]
    assert data_sources.get_structured_items(datasource) == [
        {'id': '1', 'text': 'foo'},
        {'id': '2', 'text': 'bar'},
    ]


def test_jsonvalue_datasource_with_template_error(pub, emails):
    template = '{% if form_var_foo %}[{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}]{% else %}'
    datasource = {
        'type': 'jsonvalue',
        'value': template,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary == "Data source: JSON data source ('%s') gave a template syntax error" % template
    )


def test_json_datasource(pub, requests_pub, freezer):
    get_request().datasources_cache = {}
    datasource = {'type': 'json', 'value': ''}
    assert data_sources.get_items(datasource) == []

    # missing file
    get_request().datasources_cache = {}
    datasource = {'type': 'json', 'value': 'https://example.net'}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', status=404)
        assert data_sources.get_items(datasource) == []

    # invalid json file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', body=codecs.encode(b'foobar', 'zlib_codec'))
        assert data_sources.get_items(datasource) == []

    # empty json file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={})
        assert data_sources.get_items(datasource) == []

    # unrelated json file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json='foobar')
        assert data_sources.get_items(datasource) == []

    # another unrelated json file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'data': 'foobar'})
        assert data_sources.get_items(datasource) == []

    # json file not using dictionaries
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'data': [['1', 'foo'], ['2', 'bar']]})
        assert data_sources.get_items(datasource) == []

    # a good json file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]}
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar'}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]

    # a json file with additional keys
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'data': [{'id': '1', 'text': 'foo', 'more': 'xxx'}, {'id': '2', 'text': 'bar', 'more': 'yyy'}]
            },
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'more': 'xxx'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'more': 'yyy'}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'more': 'xxx'},
            {'id': '2', 'text': 'bar', 'more': 'yyy'},
        ]

        # json specified with a variadic url

        class JsonUrlPath:
            def get_substitution_variables(self):
                return {'json_url': 'https://example.net'}

        pub.substitutions.feed(JsonUrlPath())
        datasource = {'type': 'json', 'value': '[json_url]'}
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'more': 'xxx'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'more': 'yyy'}),
        ]

        # same with django templated url
        pub.substitutions.feed(JsonUrlPath())
        datasource = {'type': 'json', 'value': '{{ json_url }}'}
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'more': 'xxx'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'more': 'yyy'}),
        ]

        # json specified with a variadic url with an erroneous space
        pub.substitutions.feed(JsonUrlPath())
        datasource = {'type': 'json', 'value': ' [json_url]'}
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'more': 'xxx'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'more': 'yyy'}),
        ]

        # same with django templated url
        pub.substitutions.feed(JsonUrlPath())
        datasource = {'type': 'json', 'value': ' {{ json_url }}'}
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'more': 'xxx'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'more': 'yyy'}),
        ]

    # a json file with integer as 'id'
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'data': [{'id': 1, 'text': 'foo'}, {'id': 2, 'text': 'bar'}]})
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': 1, 'text': 'foo'}),
            ('2', 'bar', '2', {'id': 2, 'text': 'bar'}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': 1, 'text': 'foo'},
            {'id': 2, 'text': 'bar'},
        ]

    # a json file with empty or no text values
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'data': [{'id': '1', 'text': ''}, {'id': '2'}]})
        assert data_sources.get_items(datasource) == [
            ('1', '', '1', {'id': '1', 'text': ''}),
            ('2', '2', '2', {'id': '2', 'text': '2'}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': ''},
            {'id': '2', 'text': '2'},
        ]

    # a json file with empty or no id
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '', 'text': 'foo'}, {'text': 'bar'}, {'id': None}]}
        )
        assert data_sources.get_items(datasource) == []
        assert data_sources.get_structured_items(datasource) == []

    # a json file with invalid datatype for the text entry, (list in text key),
    # the invalid entry will be skipped
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '1', 'text': ['foo']}, {'id': '2', 'text': 'bar'}]}
        )
        assert data_sources.get_items(datasource) == [('2', 'bar', '2', {'id': '2', 'text': 'bar'})]
        assert data_sources.get_structured_items(datasource) == [{'id': '2', 'text': 'bar'}]

    # specify data_attribute
    datasource = {'type': 'json', 'value': ' {{ json_url }}', 'data_attribute': 'results'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'results': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]}
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]

        get_request().datasources_cache = {}
        datasource = {'type': 'json', 'value': ' {{ json_url }}', 'data_attribute': 'data'}
        assert data_sources.get_structured_items(datasource) == []

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={'data': {'results': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]}},
        )
        assert data_sources.get_structured_items(datasource) == []

        datasource = {'type': 'json', 'value': ' {{ json_url }}', 'data_attribute': 'data.results'}
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]

    # specify id_attribute
    datasource = {'type': 'json', 'value': ' {{ json_url }}', 'id_attribute': 'pk'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'pk': '1', 'text': 'foo'}, {'pk': '2', 'text': 'bar'}]}
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'pk': '1'},
            {'id': '2', 'text': 'bar', 'pk': '2'},
        ]

        get_request().datasources_cache = {}
        datasource = {'type': 'json', 'value': ' {{ json_url }}', 'id_attribute': 'id'}
        assert data_sources.get_structured_items(datasource) == []

    # specify text_attribute
    datasource = {'type': 'json', 'value': ' {{ json_url }}', 'text_attribute': 'label'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '1', 'label': 'foo'}, {'id': '2', 'label': 'bar'}]}
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'label': 'foo'},
            {'id': '2', 'text': 'bar', 'label': 'bar'},
        ]

        get_request().datasources_cache = {}
        datasource = {'type': 'json', 'value': ' {{ json_url }}', 'text_attribute': 'text'}
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': '1', 'label': 'foo'},
            {'id': '2', 'text': '2', 'label': 'bar'},
        ]

    # specify id_attribute and text_attribute with dotted names
    datasource = {
        'type': 'json',
        'value': ' {{ json_url }}',
        'id_attribute': 'a.id',
        'text_attribute': 'a.label',
    }
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'data': [
                    {'a': {'id': '1', 'label': 'foo'}},
                    {'a': {'id': '2', 'label': 'bar'}},
                    {},
                    {'a': {'id': None}},
                    {'a': {'id': '3'}},
                    {'a': {'id': '4', 'label': ''}},
                ]
            },
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'a': {'id': '1', 'label': 'foo'}},
            {'id': '2', 'text': 'bar', 'a': {'id': '2', 'label': 'bar'}},
            {'id': '3', 'text': '3', 'a': {'id': '3'}},
            {'id': '4', 'text': '', 'a': {'id': '4', 'label': ''}},
        ]

    # check django cache
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': '{{ json_url }}'}
    data_source.cache_duration = '100'
    data_source.store()
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]}
        )
        assert data_sources.get_structured_items({'type': data_source.slug}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 1

        get_request().datasources_cache = {}
        assert data_sources.get_structured_items({'type': data_source.slug}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 1  # cache was used

        get_request().datasources_cache = {}
        data_source.text_attribute = 'id'
        freezer.move_to(datetime.timedelta(seconds=1))  # make sure mtime is different
        data_source.store()
        assert data_sources.get_structured_items({'type': data_source.slug}) == [
            {'id': '1', 'text': '1'},
            {'id': '2', 'text': '2'},
        ]
        assert len(rsps.calls) == 2  # cache was not used as parameters were different

        get_request().datasources_cache = {}
        assert data_sources.get_structured_items({'type': data_source.slug}) == [
            {'id': '1', 'text': '1'},
            {'id': '2', 'text': '2'},
        ]
        assert len(rsps.calls) == 2  # cache was used

        get_request().datasources_cache = {}
        data_source.cache_duration = '120'
        freezer.move_to(datetime.timedelta(seconds=1))  # make sure mtime is different
        data_source.store()
        assert data_sources.get_structured_items({'type': data_source.slug}) == [
            {'id': '1', 'text': '1'},
            {'id': '2', 'text': '2'},
        ]
        assert len(rsps.calls) == 3  # cache was not used as cache duration was changed

    # URL with non-ascii characters
    datasource = {'type': 'json', 'value': 'https://example.net/éléphant'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net/éléphant',
            json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]},
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar'}),
        ]


def test_json_datasource_data_attribute(pub, requests_pub):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foobar')
    datasource.data_source = {'type': 'json', 'value': 'https://example.net/'}
    datasource.id_parameter = 'id'
    datasource.data_attribute = 'results'
    datasource.store()
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net/?id=2', json={'results': [{'id': '2', 'text': 'bar'}]})
        assert datasource.get_structured_value('2') == {'id': '2', 'text': 'bar'}


def test_json_datasource_bad_url(pub, error_email, http_requests, emails):
    datasource = {'type': 'json', 'value': 'http://remote.example.net/404'}
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 0

    datasource = {
        'type': 'json',
        'value': 'http://remote.example.net/404',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 1
    assert (
        '[ERROR] Data source: Error loading JSON data source '
        '(error in HTTP request to http://remote.example.net/404 (status: 404))'
        in emails.get_latest('subject')
    )
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary == 'Data source: Error loading JSON data source '
        '(error in HTTP request to http://remote.example.net/404 (status: 404))'
    )

    datasource = {
        'type': 'json',
        'value': 'http://remote.example.net/xml',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 2
    assert 'Error reading JSON data source' in emails.get_latest('subject')
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == 'Data source: Error reading JSON data source output (Expecting value: line 1 column 1 (char 0))'
    )

    datasource = {
        'type': 'json',
        'value': 'http://remote.example.net/connection-error',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert LoggedError.count() == 3
    logged_error = LoggedError.select(order_by='id')[2]
    assert logged_error.workflow_id is None
    assert logged_error.summary.startswith('Data source: Error loading JSON data source (error')

    datasource = {
        'type': 'json',
        'value': 'http://remote.example.net/json-list-err1',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error reading JSON data source output (err 1)' in emails.get_latest('subject')
    assert LoggedError.count() == 4
    logged_error = LoggedError.select(order_by='id')[3]
    assert logged_error.workflow_id is None
    assert logged_error.summary == 'Data source: Error reading JSON data source output (err 1)'


def test_json_datasource_bad_url_scheme(pub, error_email, emails):
    datasource = {'type': 'json', 'value': '', 'notify_on_errors': True, 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 0
    assert LoggedError.count() == 0

    datasource = {'type': 'json', 'value': 'foo://bar', 'notify_on_errors': True, 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'invalid scheme in URL' in emails.get_latest('subject')
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary == 'Data source: Error loading JSON data source '
        '(invalid scheme in URL "foo://bar")'
    )

    datasource = {
        'type': 'json',
        'value': '{{blah}}/bla/blo',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'invalid URL "/bla/blo", maybe using missing variables' in emails.get_latest('subject')
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary == 'Data source: Error loading JSON data source '
        '(invalid URL "/bla/blo", maybe using missing variables)'
    )


def test_json_datasource_bad_url_no_signature(pub, error_email, http_requests, emails):
    pub.load_site_options()
    pub.site_options.set('wscall-secrets', 'remote.example.net', 'yyy')

    datasource = {
        'type': 'json',
        'value': 'http://remote.example.net/404',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 1
    assert (
        '[ERROR] Data source: Error loading JSON data source '
        '(error in HTTP request to http://remote.example.net/404 (status: 404))'
        in emails.get_latest('subject')
    )


@pytest.mark.parametrize('notify', [True, False])
@pytest.mark.parametrize('record', [True, False])
def test_json_datasource_bad_qs_data(pub, error_email, emails, notify, record):
    datasource = {
        'type': 'json',
        'value': 'https://whatever.com/json',
        'qs_data': {'foo': '{% for invalid %}', 'bar': '{{ valid }}'},
        'notify_on_errors': notify,
        'record_on_errors': record,
    }
    with responses.RequestsMock() as rsps:
        rsps.get('https://whatever.com/json', json={'data': [{'id': '1', 'text': 'foo'}]})
        assert data_sources.get_items(datasource) == [('1', 'foo', '1', {'id': '1', 'text': 'foo'})]
        assert rsps.calls[-1].request.url == 'https://whatever.com/json?bar='
    message = 'Data source: Failed to compute value "{% for invalid %}" for "foo" query parameter'
    if notify:
        assert emails.count() == 1
        assert message in emails.get_latest('subject')
    else:
        assert emails.count() == 0
    if record:
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == message
    else:
        assert LoggedError.count() == 0


def test_geojson_datasource(pub, requests_pub):
    get_request()
    get_request().datasources_cache = {}
    datasource = {'type': 'geojson', 'value': 'https://example.net'}
    assert data_sources.get_items(datasource) == []

    # missing file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', status=404)
        assert data_sources.get_items(datasource) == []

    # invalid geojson file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', body=codecs.encode(b'foobar', 'zlib_codec'))
        assert data_sources.get_items(datasource) == []

    # empty geojson file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={})
        assert data_sources.get_items(datasource) == []

    # unrelated geojson file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json='foobar')
        assert data_sources.get_items(datasource) == []

    # another unrelated geojson file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'features': 'foobar'})
        assert data_sources.get_items(datasource) == []

    # a good geojson file
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '1', 'text': 'foo'}},
                    {'properties': {'id': '2', 'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo'}}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar'}}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo'}},
            {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar'}},
        ]

    # a geojson file with additional keys
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
                    {'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
                ]
            },
        )
        assert data_sources.get_items(datasource) == [
            (
                '1',
                'foo',
                '1',
                {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            ),
            (
                '2',
                'bar',
                '2',
                {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
            ),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
        ]

        # geojson specified with a variadic url
        class GeoJSONUrlPath:
            def get_substitution_variables(self):
                return {'geojson_url': 'https://example.net'}

        pub.substitutions.feed(GeoJSONUrlPath())
        datasource = {'type': 'geojson', 'value': '[geojson_url]'}
        assert data_sources.get_items(datasource) == [
            (
                '1',
                'foo',
                '1',
                {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            ),
            (
                '2',
                'bar',
                '2',
                {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
            ),
        ]

        # same with django templated url
        pub.substitutions.feed(GeoJSONUrlPath())
        datasource = {'type': 'geojson', 'value': '{{ geojson_url }}'}
        assert data_sources.get_items(datasource) == [
            (
                '1',
                'foo',
                '1',
                {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            ),
            (
                '2',
                'bar',
                '2',
                {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
            ),
        ]

        # geojson specified with a variadic url with an erroneous space
        pub.substitutions.feed(GeoJSONUrlPath())
        datasource = {'type': 'geojson', 'value': ' [geojson_url]'}
        assert data_sources.get_items(datasource) == [
            (
                '1',
                'foo',
                '1',
                {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            ),
            (
                '2',
                'bar',
                '2',
                {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
            ),
        ]

        # same with django templated url
        pub.substitutions.feed(GeoJSONUrlPath())
        datasource = {'type': 'geojson', 'value': ' {{ geojson_url }}'}
        assert data_sources.get_items(datasource) == [
            (
                '1',
                'foo',
                '1',
                {'id': '1', 'text': 'foo', 'properties': {'id': '1', 'text': 'foo', 'more': 'xxx'}},
            ),
            (
                '2',
                'bar',
                '2',
                {'id': '2', 'text': 'bar', 'properties': {'id': '2', 'text': 'bar', 'more': 'yyy'}},
            ),
        ]

    # a geojson file with integer as 'id'
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': 1, 'text': 'foo'}},
                    {'properties': {'id': 2, 'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': 1, 'text': 'foo', 'properties': {'id': 1, 'text': 'foo'}}),
            ('2', 'bar', '2', {'id': 2, 'text': 'bar', 'properties': {'id': 2, 'text': 'bar'}}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': 1, 'text': 'foo', 'properties': {'id': 1, 'text': 'foo'}},
            {'id': 2, 'text': 'bar', 'properties': {'id': 2, 'text': 'bar'}},
        ]

    # a geojson file with escapable content in text
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': 1, 'text': 'fo\'o'}},
                    {'properties': {'id': 2, 'text': 'b<a>r'}},
                ]
            },
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'fo\'o', '1', {'id': 1, 'text': 'fo\'o', 'properties': {'id': 1, 'text': 'fo\'o'}}),
            ('2', 'b<a>r', '2', {'id': 2, 'text': 'b<a>r', 'properties': {'id': 2, 'text': 'b<a>r'}}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': 1, 'text': 'fo\'o', 'properties': {'id': 1, 'text': 'fo\'o'}},
            {'id': 2, 'text': 'b<a>r', 'properties': {'id': 2, 'text': 'b<a>r'}},
        ]

    # a geojson file with empty or no text values
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={'features': [{'properties': {'id': '1', 'text': ''}}, {'properties': {'id': '2'}}]},
        )
        assert data_sources.get_items(datasource) == [
            ('1', '1', '1', {'id': '1', 'text': '1', 'properties': {'id': '1', 'text': ''}}),
            ('2', '2', '2', {'id': '2', 'text': '2', 'properties': {'id': '2'}}),
        ]
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': '1', 'properties': {'id': '1', 'text': ''}},
            {'id': '2', 'text': '2', 'properties': {'id': '2'}},
        ]

    # a geojson file with empty or no id
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '', 'text': 'foo'}},
                    {'properties': {'text': 'bar'}},
                    {'properties': {'id': None}},
                ]
            },
        )
        assert data_sources.get_items(datasource) == []
        assert data_sources.get_structured_items(datasource) == []

    # specify id_property
    datasource = {'type': 'geojson', 'value': ' {{ geojson_url }}', 'id_property': 'gid'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'gid': '1', 'text': 'foo'}},
                    {'properties': {'gid': '2', 'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'properties': {'gid': '1', 'text': 'foo'}},
            {'id': '2', 'text': 'bar', 'properties': {'gid': '2', 'text': 'bar'}},
        ]

        # check with missing id property
        get_request().datasources_cache = {}
        datasource = {'type': 'geojson', 'value': ' {{ geojson_url }}', 'id_property': 'id'}
        assert data_sources.get_structured_items(datasource) == []

    # check with feature IDs
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'id': '1', 'properties': {'text': 'foo'}},
                    {'id': '2', 'properties': {'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'foo', 'properties': {'text': 'foo'}},
            {'id': '2', 'text': 'bar', 'properties': {'text': 'bar'}},
        ]

    # specify label_template_property
    datasource = {
        'type': 'geojson',
        'value': ' {{ geojson_url }}',
        'label_template_property': '{{ id }}: {{ text }}',
    }
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '1', 'text': 'foo'}},
                    {'properties': {'id': '2', 'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': '1: foo', 'properties': {'id': '1', 'text': 'foo'}},
            {'id': '2', 'text': '2: bar', 'properties': {'id': '2', 'text': 'bar'}},
        ]

        # wrong template
        datasource = {
            'type': 'geojson',
            'value': ' {{ geojson_url }}',
            'label_template_property': '{{ text }',
        }
        get_request().datasources_cache = {}
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': '{{ text }', 'properties': {'id': '1', 'text': 'foo'}},
            {'id': '2', 'text': '{{ text }', 'properties': {'id': '2', 'text': 'bar'}},
        ]

        datasource = {'type': 'geojson', 'value': ' {{ geojson_url }}', 'label_template_property': 'text'}
        get_request().datasources_cache = {}
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': 'text', 'properties': {'id': '1', 'text': 'foo'}},
            {'id': '2', 'text': 'text', 'properties': {'id': '2', 'text': 'bar'}},
        ]

    # unknown property or empty value
    datasource = {'type': 'geojson', 'value': ' {{ geojson_url }}', 'label_template_property': '{{ label }}'}
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '1', 'text': 'foo', 'label': ''}},
                    {'properties': {'id': '2', 'text': 'bar'}},
                ]
            },
        )
        assert data_sources.get_structured_items(datasource) == [
            {'id': '1', 'text': '1', 'properties': {'id': '1', 'text': 'foo', 'label': ''}},
            {'id': '2', 'text': '2', 'properties': {'id': '2', 'text': 'bar'}},
        ]


def test_geojson_datasource_bad_url(pub, http_requests, error_email, emails):
    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/404',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'status: 404' in emails.get_latest('subject')
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary == 'Data source: Error loading JSON data source '
        '(error in HTTP request to http://remote.example.net/404 (status: 404))'
    )

    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/xml',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error reading JSON data source output' in emails.get_latest('subject')
    assert 'Expecting value:' in emails.get_latest('subject')
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == 'Data source: Error reading JSON data source output (Expecting value: line 1 column 1 (char 0))'
    )

    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/connection-error',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'error' in emails.get_latest('subject')
    assert LoggedError.count() == 3
    logged_error = LoggedError.select(order_by='id')[2]
    assert logged_error.workflow_id is None
    assert logged_error.summary.startswith('Data source: Error loading JSON data source (error')

    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/json-list-err1',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error reading JSON data source output (err 1)' in emails.get_latest('subject')
    assert LoggedError.count() == 4
    logged_error = LoggedError.select(order_by='id')[3]
    assert logged_error.workflow_id is None
    assert logged_error.summary == 'Data source: Error reading JSON data source output (err 1)'

    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/json-list-err1bis',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error reading JSON data source output (err_desc :()' in emails.get_latest('subject')
    assert LoggedError.count() == 5
    logged_error = LoggedError.select(order_by='id')[4]
    assert logged_error.workflow_id is None
    assert logged_error.summary == 'Data source: Error reading JSON data source output (err_desc :()'

    datasource = {
        'type': 'geojson',
        'value': 'http://remote.example.net/json-list-errstr',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert (
        'Error reading JSON data source output (err_desc :(, err_class foo_bar, err bug)'
        in emails.get_latest('subject')
    )
    assert LoggedError.count() == 6
    logged_error = LoggedError.select(order_by='id')[5]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == 'Data source: Error reading JSON data source output (err_desc :(, err_class foo_bar, err bug)'
    )


def test_geojson_datasource_bad_url_scheme(pub, error_email, emails):
    datasource = {'type': 'geojson', 'value': ''}
    assert data_sources.get_items(datasource) == []
    assert emails.count() == 0

    datasource = {'type': 'geojson', 'value': 'foo://bar', 'notify_on_errors': True, 'record_on_errors': True}
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'invalid scheme in URL' in emails.get_latest('subject')
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary
        == 'Data source: Error loading JSON data source (invalid scheme in URL "foo://bar")'
    )

    datasource = {
        'type': 'geojson',
        'value': '{{blah}}/bla/blo',
        'notify_on_errors': True,
        'record_on_errors': True,
    }
    assert data_sources.get_items(datasource) == []
    assert 'Error loading JSON data source' in emails.get_latest('subject')
    assert 'invalid URL "/bla/blo", maybe using missing variables' in emails.get_latest('subject')
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.workflow_id is None
    assert (
        logged_error.summary == 'Data source: Error loading JSON data source '
        '(invalid URL "/bla/blo", maybe using missing variables)'
    )


def test_data_source_slug_name(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foo bar')
    data_source.store()
    assert data_source.slug == 'foo_bar'


def test_data_source_new_id(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foo bar')
    data_source.store()
    assert data_source.id == 1
    data_source = NamedDataSource(name='foo bar2')
    data_source.store()
    assert data_source.id == 2
    data_source.remove_self()
    data_source = NamedDataSource(name='foo bar3')
    data_source.store()
    assert data_source.id == 3
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foo bar4')
    data_source.store()
    assert data_source.id == 1


def test_optional_item_field_with_data_source(requests_pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]),
    }
    data_source.store()

    field = fields.ItemField()
    field.id = 1
    field.required = False
    field.data_source = {
        'type': 'foobar',  # use the named data source defined earlier
    }
    form = Form()
    field.add_to_form(form)
    widget = form.get_widget('f1')
    assert widget is not None
    assert widget.options == [('1', 'un', '1'), ('2', 'deux', '2')]


@pytest.mark.parametrize('qs_data', [{}, {'arg1': 'val1', 'arg2': 'val2'}])
def test_data_source_signed(no_request_pub, qs_data):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'https://api.example.com/json'}
    data_source.qs_data = qs_data
    data_source.store()

    with responses.RequestsMock() as rsps:
        rsps.get('https://api.example.com/json', json={'data': [{'id': 0, 'text': 'zero'}]})
        assert len(data_sources.get_items({'type': 'foobar'})) == 1
        signed_url = rsps.calls[-1].request.url
    assert signed_url.startswith('https://api.example.com/json?')
    parsed = urllib.parse.urlparse(signed_url)
    querystring = urllib.parse.parse_qs(parsed.query)
    # stupid simple (but sufficient) signature test:
    assert querystring['algo'] == ['sha256']
    assert querystring['orig'] == ['example.net']
    assert querystring['nonce'][0]
    assert querystring['timestamp'][0]
    assert querystring['signature'][0]
    if qs_data:
        assert querystring['arg1'][0] == 'val1'
        assert querystring['arg2'][0] == 'val2'

    data_source.data_source = {'type': 'json', 'value': 'https://api.example.com/json?foo=bar'}
    data_source.store()
    with responses.RequestsMock() as rsps:
        rsps.get('https://api.example.com/json', json={'data': [{'id': 0, 'text': 'zero'}]})
        assert len(data_sources.get_items({'type': 'foobar'})) == 1
        signed_url = rsps.calls[-1].request.url
    assert signed_url.startswith('https://api.example.com/json?')
    parsed = urllib.parse.urlparse(signed_url)
    querystring = urllib.parse.parse_qs(parsed.query)
    assert querystring['algo'] == ['sha256']
    assert querystring['orig'] == ['example.net']
    assert querystring['nonce'][0]
    assert querystring['timestamp'][0]
    assert querystring['signature'][0]
    assert querystring['foo'][0] == 'bar'
    if qs_data:
        assert querystring['arg1'][0] == 'val1'
        assert querystring['arg2'][0] == 'val2'

    # with empty parameter
    data_source.data_source = {'type': 'json', 'value': 'https://api.example.com/json?foo=bar&baz='}
    data_source.store()
    with responses.RequestsMock() as rsps:
        rsps.get('https://api.example.com/json', json={'data': [{'id': 0, 'text': 'zero'}]})
        assert len(data_sources.get_items({'type': 'foobar'})) == 1
        signed_url = rsps.calls[-1].request.url
    assert signed_url.startswith('https://api.example.com/json?')
    parsed = urllib.parse.urlparse(signed_url)
    querystring = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    assert querystring['algo'] == ['sha256']
    assert querystring['orig'] == ['example.net']
    assert querystring['nonce'][0]
    assert querystring['timestamp'][0]
    assert querystring['signature'][0]
    assert querystring['foo'][0] == 'bar'
    assert querystring['baz'] == ['']
    if qs_data:
        assert querystring['arg1'][0] == 'val1'
        assert querystring['arg2'][0] == 'val2'

    data_source.data_source = {'type': 'json', 'value': 'https://no-secret.example.com/json'}
    data_source.store()
    with responses.RequestsMock() as rsps:
        rsps.get('https://no-secret.example.com/json', json={'data': [{'id': 0, 'text': 'zero'}]})
        assert len(data_sources.get_items({'type': 'foobar'})) == 1
        unsigned_url = rsps.calls[-1].request.url
    if qs_data:
        assert unsigned_url == 'https://no-secret.example.com/json?arg1=val1&arg2=val2'
    else:
        assert unsigned_url == 'https://no-secret.example.com/json'

    data_source.data_source = {'type': 'json', 'value': 'https://no-secret.example.com/json?foo=bar'}
    data_source.store()
    with responses.RequestsMock() as rsps:
        rsps.get('https://no-secret.example.com/json', json={'data': [{'id': 0, 'text': 'zero'}]})
        assert len(data_sources.get_items({'type': 'foobar'})) == 1
        unsigned_url = rsps.calls[-1].request.url
    if qs_data:
        assert unsigned_url == 'https://no-secret.example.com/json?foo=bar&arg1=val1&arg2=val2'
    else:
        assert unsigned_url == 'https://no-secret.example.com/json?foo=bar'


def test_named_datasource_json_cache(requests_pub):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foobar')
    datasource.data_source = {'type': 'json', 'value': 'http://whatever/'}
    datasource.store()

    with responses.RequestsMock() as rsps:
        rsps.get('http://whatever/', json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]})

        assert data_sources.get_structured_items({'type': 'foobar'}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 1

        get_request().datasources_cache = {}
        assert data_sources.get_structured_items({'type': 'foobar'}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 2

        datasource.cache_duration = '60'
        datasource.store()

        # will cache
        get_request().datasources_cache = {}
        assert data_sources.get_structured_items({'type': 'foobar'}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 3

        # will get from cache
        get_request().datasources_cache = {}
        assert data_sources.get_structured_items({'type': 'foobar'}) == [
            {'id': '1', 'text': 'foo'},
            {'id': '2', 'text': 'bar'},
        ]
        assert len(rsps.calls) == 3


def test_named_datasource_id_parameter(requests_pub):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foobar')
    datasource.data_source = {'type': 'json', 'value': 'http://whatever/'}
    datasource.id_parameter = 'id'
    datasource.store()

    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value})

        assert datasource.get_structured_value('1') == value[0]
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://whatever/?id=1'

        # try again, get from request.datasources_cache
        assert datasource.get_structured_value('1') == value[0]
        assert len(rsps.calls) == 1  # no new call

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'bar'}, {'id': '2', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value})
        assert datasource.get_structured_value('1') == value[0]
        assert len(rsps.calls) == 1

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('http://whatever/', json={'data': []})  # empty list
        assert datasource.get_structured_value('1') is None
        assert len(rsps.calls) == 1

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value})
        assert datasource.get_structured_value('1') == value[0]
        assert len(rsps.calls) == 1

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value, 'err': 1})
        assert datasource.get_structured_value('1') is None
        assert len(rsps.calls) == 1
        # no cache for errors
        assert datasource.get_structured_value('1') is None
        assert len(rsps.calls) == 2  # called again

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = {'id': '1', 'text': 'foo'}  # not a list
        rsps.get('http://whatever/', json={'data': value})
        assert datasource.get_structured_value('1') is None
        assert len(rsps.calls) == 1

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        rsps.get('http://whatever/', body='not json')
        assert datasource.get_structured_value('1') is None
        assert len(rsps.calls) == 1

    # ws badly configured, return all items
    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'bar'}, {'id': '2', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value})
        assert datasource.get_structured_value('2') == value[1]
        assert len(rsps.calls) == 1
        # try again, get from request.datasources_cache
        assert datasource.get_structured_value('2') == value[1]
        assert len(rsps.calls) == 1

    get_request().datasources_cache = {}
    with responses.RequestsMock() as rsps:
        value = [{'id': '1', 'text': 'bar'}, {'id': '2', 'text': 'foo'}]
        rsps.get('http://whatever/', json={'data': value})
        assert datasource.get_structured_value('3') is None
        assert len(rsps.calls) == 1
        # try again, get from request.datasources_cache
        assert datasource.get_structured_value('3') is None
        assert len(rsps.calls) == 1  # no new call


def test_named_datasource_in_formdef(pub):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foobar')
    datasource.data_source = {'type': 'json', 'value': 'http://whatever/'}
    datasource.store()
    assert datasource.slug == 'foobar'

    formdef = FormDef()
    assert not any(datasource.usage_in_formdef(formdef))

    formdef.fields = [
        fields.ItemField(id='0', label='string', data_source={'type': 'foobar'}),
    ]
    assert any(datasource.usage_in_formdef(formdef))

    datasource.slug = 'barfoo'
    assert not any(datasource.usage_in_formdef(formdef))


def test_data_source_in_template(pub):
    NamedDataSource.wipe()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow_options = {'foo': 'hello'}
    formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'hello'}
    formdata.store()
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'https://example.invalid/json?t={{form_var_foo}}'}
    data_source.store()

    with pub.complex_data():
        with responses.RequestsMock() as rsps:
            rsps.get(
                'https://example.invalid/json',
                json={
                    'data': [{'id': 0, 'text': 'zéro'}, {'id': 1, 'text': 'uné'}, {'id': 2, 'text': 'deux'}],
                    'meta': {
                        'foo': 'bar',
                        'blah': {'a': 'b', 'c': 'd'},
                    },
                },
            )

            assert (
                WorkflowStatusItem.compute('{{ data_source.foobar|first|get:"text" }}', allow_complex=True)
                == 'zéro'
            )
            assert rsps.calls[-1].request.url == 'https://example.invalid/json?t=hello'
            assert WorkflowStatusItem.compute('{{ data_source.foobar.meta.foo }}') == 'bar'
            assert WorkflowStatusItem.compute('{{ data_source.foobar.meta.blah }}') == "{'a': 'b', 'c': 'd'}"
            assert WorkflowStatusItem.compute('{{ data_source.foobar.meta.blah.c }}') == 'd'

            # check __getitem__
            assert WorkflowStatusItem.compute('{{ data_source.foobar.0.id }}') == '0'

            # check |getlist
            assert (
                WorkflowStatusItem.compute(
                    '{% if "zéro" in data_source.foobar|getlist:"text" %}hello{% endif %}'
                )
                == 'hello'
            )
            assert (
                WorkflowStatusItem.compute(
                    '{% if "plop" in data_source.foobar|getlist:"text" %}hello{% endif %}'
                )
                == ''
            )


def export_to_indented_xml(data_source, include_id=False):
    data_source_xml = data_source.export_to_xml(include_id=include_id)
    ET.indent(data_source_xml)
    return data_source_xml


def assert_import_export_works(data_source, include_id=False):
    data_source2 = NamedDataSource.import_from_xml_tree(
        ET.fromstring(ET.tostring(data_source.export_to_xml(include_id))), include_id
    )
    assert ET.tostring(export_to_indented_xml(data_source)) == ET.tostring(
        export_to_indented_xml(data_source2)
    )
    return data_source2


def test_data_source(pub):
    data_source = NamedDataSource(name='test')
    assert_import_export_works(data_source, include_id=True)


def test_data_source_with_category(pub):
    category = DataSourceCategory(name='test category')
    category.store()

    data_source = NamedDataSource(name='test category')
    data_source.category_id = str(category.id)
    data_source.store()
    data_source2 = assert_import_export_works(data_source, include_id=True)
    assert data_source2.category_id == data_source.category_id

    # import with non existing category
    DataSourceCategory.wipe()
    export = ET.tostring(data_source.export_to_xml(include_id=True))
    data_source3 = NamedDataSource.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert data_source3.category_id is None


def test_data_source_with_qs_data(pub):
    data_source = NamedDataSource(name='test')
    data_source.qs_data = {'arg1': 'val1', 'arg2': 'val2'}
    data_source.store()
    data_source2 = assert_import_export_works(data_source, include_id=True)
    assert data_source2.qs_data == {'arg1': 'val1', 'arg2': 'val2'}


def test_missing_named_data_source(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource.get_by_slug('foo_bar', stub_fallback=True)
    assert data_source.name == 'foo_bar'
    assert '#invalid' in data_source.get_admin_url()
    assert data_sources.get_structured_items(data_source.data_source) == []


def test_json_datasource_publik_caller_url(pub, requests_pub):
    get_request().datasources_cache = {}
    datasource = {'type': 'json', 'value': 'https://passerelle.invalid/json/'}
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://passerelle.invalid/json/',
            json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]},
        )
        assert data_sources.get_items(datasource) == [
            ('1', 'foo', '1', {'id': '1', 'text': 'foo'}),
            ('2', 'bar', '2', {'id': '2', 'text': 'bar'}),
        ]
        # no form available, the header is empty
        assert rsps.calls[0].request.headers['Publik-Caller-URL'] == ''


def test_datasource_get_dependencies(pub):
    NamedDataSource.wipe()
    pub.role_class.wipe()

    data_source = NamedDataSource(name='empty')
    assert not list(data_source.get_dependencies())

    role1 = pub.role_class()
    role1.name = 'Test Role'
    role1.store()

    role2 = pub.role_class()
    role2.name = 'Other Role'
    role2.store()

    role3 = pub.role_class()
    role3.name = 'Unused Role'
    role3.store()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'wcs:users'}
    data_source.users_excluded_roles = [role1.id]
    data_source.users_included_roles = [role2.id]
    data_source.store()

    assert {x.name for x in data_source.get_dependencies() if isinstance(x, pub.role_class)} == {
        'Test Role',
        'Other Role',
    }
