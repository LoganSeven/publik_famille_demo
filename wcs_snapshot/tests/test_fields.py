import json
import os
import re
import time
from unittest import mock

import pytest
import responses
from bs4 import BeautifulSoup
from pyquery import PyQuery
from quixote.http_request import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon import sessions
from wcs.qommon.form import Form, OptGroup
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.qommon.upload_storage import PicklableUpload
from wcs.variables import LazyFormData

from .utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    pub._set_request(req)
    req.session = sessions.Session(id=1)
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_fill_admin_form(pub):
    pub.root_directory.backoffice = pub.backoffice_directory_class()
    for klass in fields.base.field_classes:
        form = Form(use_tokens=False)
        klass().fill_admin_form(form, formdef=None)


def test_get_admin_attributes():
    for klass in fields.base.field_classes:
        klass().get_admin_attributes()


def test_add_to_form(pub):
    for klass in fields.base.field_classes:
        form = Form(use_tokens=False)
        if klass is fields.PageField:
            with pytest.raises(AttributeError):
                klass(label='foo').add_to_form(form)
        elif klass is fields.ComputedField:
            # no ui
            continue
        else:
            klass(label='foo').add_to_form(form)


def test_convert_from_empty_string():
    for klass in fields.base.field_classes:
        field = klass(label='foo')
        if field.convert_value_from_str:
            assert bool(field.convert_value_from_str('')) is False


def test_string(pub):
    # sample string
    assert fields.StringField().get_view_value('foo') == 'foo'
    assert fields.StringField().get_view_short_value('foo') == 'foo'
    assert fields.StringField().get_rst_view_value('foo') == 'foo'
    assert fields.StringField().get_csv_value('foo') == ['foo']
    # empty string
    assert fields.StringField().get_view_value('') == ''
    assert fields.StringField().get_view_short_value('') == ''
    assert fields.StringField().get_rst_view_value('') == ''
    assert fields.StringField().get_csv_value('') == ['']
    # url
    url = 'https://www.example.org/plop-plop-plop'
    assert (
        str(fields.StringField().get_view_value(url))
        == '<a href="https://www.example.org/plop-plop-plop" rel="nofollow">https://www.example.org/plop-plop-plop</a>'
    )
    assert (
        str(fields.StringField().get_view_short_value(url))
        == '<a href="https://www.example.org/plop-plop-plop" rel="nofollow">https://www.example.org/plop-plop-plop</a>'
    )
    assert str(fields.StringField().get_rst_view_value(url)) == url
    assert fields.StringField().get_csv_value(url) == [url]
    # hackish url
    url = 'https://www.example.org/"><script>plop</script>'
    assert (
        str(fields.StringField().get_view_value(url))
        == '<a href="https://www.example.org/" rel="nofollow">https://www.example.org/</a>&quot;&gt;&lt;script&gt;plop&lt;/script&gt;'
    )
    assert (
        str(fields.StringField().get_view_short_value(url))
        == '<a href="https://www.example.org/" rel="nofollow">https://www.example.org/</a>&quot;&gt;&lt;script&gt;plop&lt;/script&gt;'
    )
    assert str(fields.StringField().get_rst_view_value(url)) == url
    assert fields.StringField().get_csv_value(url) == [url]
    # bad value
    assert (
        fields.StringField().get_view_value({'@type': 'computed', 'data': '42'})
        == "{'@type': 'computed', 'data': '42'}"
    )
    assert (
        fields.StringField().get_view_short_value({'@type': 'computed', 'data': '42'})
        == "{'@type': 'computed', 'data': '42'}"
    )
    assert (
        fields.StringField().get_rst_view_value({'@type': 'computed', 'data': '42'})
        == "{'@type': 'computed', 'data': '42'}"
    )
    assert fields.StringField().get_csv_value({'@type': 'computed', 'data': '42'}) == [
        "{'@type': 'computed', 'data': '42'}"
    ]


def test_text(pub):
    assert fields.TextField().get_view_short_value('foo' * 15) == ('foo' * 10)[:27] + '(…)'
    assert fields.TextField().get_view_value('foo') == '<p>foo</p>'
    assert fields.TextField().get_view_value('foo\n\nfoo') == '<p>foo\n</p><p>\nfoo</p>'
    assert fields.TextField(display_mode='pre').get_view_value('foo') == '<p class="plain-text-pre">foo</p>'
    assert (
        fields.TextField(display_mode='rich').get_view_short_value('<p>foo</p>' * 15)
        == ('foo' * 10)[:27] + '(…)'
    )
    assert (
        fields.TextField(display_mode='rich').get_view_value('<script></script><h1>bar</h1><p>foo</p>')
        == '<h1>bar</h1><p>foo</p>'
    )
    assert (
        fields.TextField(display_mode='basic-rich').get_view_value('<script></script><h1>bar</h1><p>foo</p>')
        == 'bar<p>foo</p>'
    )

    form = Form(use_tokens=False)
    fields.TextField().add_to_form(form)
    assert '<textarea' in str(form.render())
    assert 'cols="72"' in str(form.render())
    assert 'rows="5"' in str(form.render())

    form = Form(use_tokens=False)
    fields.TextField(cols='12', rows='12').add_to_form(form)
    assert '<textarea' in str(form.render())
    assert 'cols="12"' in str(form.render())
    assert 'rows="12"' in str(form.render())

    form = Form(use_tokens=False)
    fields.TextField(display_mode='rich').add_to_form(form)
    assert PyQuery(str(form.render()))('godo-editor[schema=full]')

    form = Form(use_tokens=False)
    fields.TextField(display_mode='basic-rich').add_to_form(form)
    assert PyQuery(str(form.render()))('godo-editor[schema=basic]')


def test_text_anonymise(pub):
    formdef = FormDef()
    formdef.name = 'title'
    formdef.fields = [fields.TextField(id='0', label='comment', varname='comment')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': 'bar'}
    formdata.anonymise()
    assert not formdata.data.get('0')

    formdef.fields[0].anonymise = 'no'
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': 'bar'}
    formdata.anonymise()
    assert formdata.data.get('0') == 'bar'


def test_email():
    assert (
        fields.EmailField().get_view_value('foo@localhost')
        == '<a href="mailto:foo@localhost">foo@localhost</a>'
    )
    assert fields.EmailField().get_rst_view_value('foo@localhost') == 'foo@localhost'


def test_bool():
    assert fields.BoolField().get_view_value(True) == 'Yes'
    assert fields.BoolField().get_view_value(False) == 'No'


def test_bool_stats(pub):
    formdef = FormDef()
    formdef.name = 'title'
    formdef.url_name = 'title'
    formdef.fields = [fields.BoolField(id='1')]
    formdef.store()
    data_class = formdef.data_class()
    formdatas = []
    for value in (True, True, True, False):
        formdata = data_class()
        formdata.data = {'1': value}
        formdatas.append(formdata)
    stats = formdef.fields[0].stats(formdatas)
    assert re.findall('Yes.*75.*No.*25', str(stats))


def test_items(pub):
    assert fields.ItemsField(items=['a', 'b', 'c']).get_view_value(['a', 'b']) == 'a, b'
    assert fields.ItemsField(items=['a', 'b', 'c']).get_csv_value(['a', 'b']) == ['a', 'b', '']
    assert len(fields.ItemsField(items=['a', 'b', 'c']).get_csv_heading()) == 3
    assert fields.ItemsField(items=['a', 'b', 'c'], max_choices=2).get_csv_value(['a', 'b']) == ['a', 'b']
    assert len(fields.ItemsField(items=['a', 'b', 'c'], max_choices=2).get_csv_heading()) == 2

    field = fields.ItemsField(label='plop')
    field.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}]',
    }
    assert field.get_options() == [('1', 'foo', '1'), ('2', 'bar', '2')]
    assert field.get_options() == [('1', 'foo', '1'), ('2', 'bar', '2')]  # twice for cached behaviour

    assert field.get_csv_heading() == [
        'plop 1 (identifier)',
        'plop 1 (label)',
        'plop 2 (identifier)',
        'plop 2 (label)',
    ]
    assert field.get_csv_value(['a', 'b'], structured_value=json.loads(field.data_source['value'])) == [
        '1',
        'foo',
        '2',
        'bar',
    ]

    # check values is cut on max choices
    field.max_choices = 1
    assert field.get_csv_heading() == ['plop 1 (identifier)', 'plop 1 (label)']
    assert field.get_csv_value(['a', 'b'], structured_value=json.loads(field.data_source['value'])) == [
        '1',
        'foo',
    ]

    # check empty columns are added if necessary
    field.max_choices = None
    field.data_source['value'] = (
        '[{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}, {"id": "3", "text": "baz"}]'
    )
    assert field.get_csv_heading() == [
        'plop 1 (identifier)',
        'plop 1 (label)',
        'plop 2 (identifier)',
        'plop 2 (label)',
        'plop 3 (identifier)',
        'plop 3 (label)',
    ]
    assert field.get_csv_value(['a', 'b'], structured_value=json.loads(field.data_source['value'])[:2]) == [
        '1',
        'foo',
        '2',
        'bar',
        '',
        '',
    ]

    # if labels are available, display using <ul>
    field = fields.ItemsField()
    view_value = field.get_view_value('a, b', value_id=['1', '2'], labels=['a', 'b'])
    elems = BeautifulSoup(str(view_value)).find('ul').find_all('li')
    assert len(elems) == 2
    assert elems[0].text == 'a'
    assert elems[1].text == 'b'


def test_items_get_value_info():
    # no data source : labels are available
    field = fields.ItemsField(id='1', items=['a', 'b', 'c'])
    assert field.get_value_info({'1': ['un', 'deux'], '1_display': 'un, deux'}) == (
        'un, deux',
        {'value_id': ['un', 'deux'], 'labels': ['un', 'deux']},
    )

    # data source with structured : labels are available
    field = fields.ItemsField(id='1')
    field.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}]',
    }
    assert field.get_value_info(
        {
            '1': ['un', 'deux'],
            '1_display': 'un, deux',
            '1_structured': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}],
        }
    ) == (
        'un, deux',
        {'value_id': ['un', 'deux'], 'labels': ['un', 'deux']},
    )

    # data source with no strucured : no labels
    field = fields.ItemsField(id='1')
    field.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "foo"}, {"id": "2", "text": "bar"}]',
    }
    assert field.get_value_info({'1': ['un', 'deux'], '1_display': 'un, deux'}) == (
        'un, deux',
        {'value_id': ['un', 'deux'], 'labels': []},
    )


def test_password():
    assert fields.PasswordField().get_view_value('xxx') == '●' * 8


def test_file():
    upload = Upload('/foo/bar', content_type='text/plain')
    assert fields.FileField().get_csv_value(upload) == ['/foo/bar']


def test_page(pub):
    formdef = FormDef()
    formdef.fields = []
    page = fields.PageField()
    assert page.is_visible({}, formdef) is True


def test_table():
    assert 'prefill' not in fields.TableField().get_admin_attributes()


def test_title(pub):
    field = fields.TitleField(label='Foobar')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None">Foobar</h3>' in str(form.render())

    field = fields.TitleField(label='Foobar', extra_css_class='test')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None" class="test">Foobar</h3>' in str(form.render())

    # test for variable substitution
    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.TitleField(label='{{ bar }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None">Foobar</h3>' in str(form.render())

    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.TitleField(label='[bar]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None">Foobar</h3>' in str(form.render())

    # test for proper escaping of substitution variables
    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.TitleField(label='{{ foo }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None">1 &lt; 3</h3>' in str(form.render())

    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.TitleField(label='[foo]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h3 data-field-id="None">1 &lt; 3</h3>' in str(form.render())

    # test for html content
    field = fields.TitleField(label='<i>Foobar&eacute;</i>')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '&lt;i&gt;Foobar&amp;eacute;&lt;/i&gt;' in str(form.render())
    assert field.unhtmled_label == 'Foobaré'


def test_subtitle(pub):
    field = fields.SubtitleField(label='Foobar')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None">Foobar</h4>' in str(form.render())

    field = fields.SubtitleField(label='Foobar', extra_css_class='test')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None" class="test">Foobar</h4>' in str(form.render())

    # test for variable substitution
    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.SubtitleField(label='{{ bar }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None">Foobar</h4>' in str(form.render())

    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.SubtitleField(label='[bar]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None">Foobar</h4>' in str(form.render())

    # test for proper escaping of substitution variables
    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.SubtitleField(label='{{ foo }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None">1 &lt; 3</h4>' in str(form.render())

    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.SubtitleField(label='[foo]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<h4 data-field-id="None">1 &lt; 3</h4>' in str(form.render())

    # test for html content
    field = fields.SubtitleField(label='<i>Foobar&eacute;</i>')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '&lt;i&gt;Foobar&amp;eacute;&lt;/i&gt;' in str(form.render())
    assert field.unhtmled_label == 'Foobaré'


def test_comment(pub):
    field = fields.CommentField(label='Foobar')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert BeautifulSoup(str(form.render())).find('div').text.strip() == 'Foobar'

    field = fields.CommentField(label='Foo\n\nBar\n\nBaz')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert re.match(  # regex to handle different beautifulsoup behaviours
        r'<div class="comment-field\s?">\n<p>Foo</p>\n<p>Bar</p>\n<p>Baz</p>\n</div>',
        str(BeautifulSoup(str(form.render())).find('div')),
    )

    # test for variable substitution
    pub.substitutions.feed(MockSubstitutionVariables())
    field = fields.CommentField(label='{{ bar }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert BeautifulSoup(str(form.render())).find('div').text.strip() == 'Foobar'

    field = fields.CommentField(label='[bar]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert BeautifulSoup(str(form.render())).find('div').text.strip() == 'Foobar'

    # test for proper escaping of substitution variables
    field = fields.CommentField(label='{{ foo }}')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '1 &lt; 3' in str(form.render())

    field = fields.CommentField(label='[foo]')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '1 &lt; 3' in str(form.render())

    # test for html content
    field = fields.CommentField(label='<p>Foobar</p>')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert '<p>Foobar</p>' in str(form.render())
    assert field.unhtmled_label == 'Foobar'

    field = fields.CommentField(label='<p>Foobar&eacute;</p>')
    assert field.unhtmled_label == 'Foobaré'


def test_map():
    assert fields.MapField().get_json_value({'lat': 42.2, 'lon': 10.2}) == {'lat': 42.2, 'lon': 10.2}
    assert fields.MapField().get_json_value({'lat': -42.2, 'lon': 10.2}) == {'lat': -42.2, 'lon': 10.2}
    assert fields.MapField().get_json_value(None) is None


def test_map_migrate():
    field = fields.MapField()
    field.init_with_geoloc = True
    assert field.migrate()
    assert field.initial_position == 'geoloc'
    assert not field.migrate()

    field = fields.MapField()
    field.default_position = '1;2'
    assert field.migrate()
    assert field.initial_position == 'point'
    assert not field.migrate()


def test_map_set_value(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.MapField(id='5', label='map', varname='map')]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}

    formdef.fields[0].set_value(formdata.data, '42;10')
    assert formdata.data['5'] == {'lat': 42, 'lon': 10}
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    keys = substvars.get_flat_keys()
    assert 'form_var_map_lon' in keys
    assert 'form_var_map_lat' in keys
    assert isinstance(substvars['form_var_map_lon'], float)
    assert int(substvars['form_var_map_lon']) == 10

    with mock.patch('wcs.qommon.misc.get_reverse_geocoding_data') as get_reverse_geocoding_data:
        get_reverse_geocoding_data.return_value = json.dumps({'address': {'house_number': '42'}})
        assert substvars['form_var_map_reverse_address_house_number'] == '42'

    formdef.fields[0].set_value(formdata.data, '')
    assert formdata.data['5'] is None
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    keys = substvars.get_flat_keys()
    assert 'form_var_map_lon' not in keys

    # set invalid value, it is ignored
    with pytest.raises(fields.SetValueError):
        formdef.fields[0].set_value(formdata.data, 'XXX;YYY')
    with pytest.raises(fields.SetValueError):
        formdef.fields[0].set_value(formdata.data, {'lat': 'XXX', 'lon': 'YYY'})


def test_item_render(pub):
    items_kwargs = []
    items_kwargs.append({'items': ['aa', 'ab', 'ac']})
    items_kwargs.append(
        {
            'data_source': {
                'type': 'jsonvalue',
                'value': '[{"id": "aa", "text": "aa"}, {"id": "ab", "text": "ab"}, {"id": "ac", "text": "ac"}]',
            }
        }
    )

    for item_kwargs in items_kwargs:
        field = fields.ItemField(id='1', label='Foobar', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('<option') == 3

        field = fields.ItemField(id='1', label='Foobar', required='optional', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('<option') == 3

        field = fields.ItemField(
            id='1', label='Foobar', required='optional', hint='Bla bla bla', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert (
            str(form.render()).count('<option value="" data-hint="Bla bla bla">Bla bla bla</option>') == 1
        )  # ---
        assert str(form.render()).count('<option') == 4

        field = fields.ItemField(
            id='1', label='Foobar', required='required', hint='Bla bla bla', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert (
            str(form.render()).count('<option value="" data-hint="Bla bla bla">Bla bla bla</option>') == 1
        )  # ---
        assert str(form.render()).count('<option') == 4

    items_kwargs = []
    items_kwargs.append({'items': None})
    items_kwargs.append({'items': []})
    items_kwargs.append({'data_source': {'type': 'jsonvalue', 'value': '[]'}})
    for item_kwargs in items_kwargs:
        field = fields.ItemField(id='1', label='Foobar', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('<option') == 1

        field = fields.ItemField(id='1', label='Foobar', required='optional', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('<option') == 1

        field = fields.ItemField(
            id='1', label='Foobar', required='optional', hint='Bla bla bla', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert (
            str(form.render()).count('<option value="" data-hint="Bla bla bla">Bla bla bla</option>') == 1
        )  # ---
        assert str(form.render()).count('<option') == 1

        field = fields.ItemField(
            id='1', label='Foobar', required='required', hint='Bla bla bla', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert (
            str(form.render()).count('<option value="" data-hint="Bla bla bla">Bla bla bla</option>') == 1
        )  # ---
        assert str(form.render()).count('<option') == 1


def test_item_render_as_autocomplete(pub):
    field = fields.ItemField(id='1', label='Foobar', items=['aa', 'ab', 'ac'], display_mode='autocomplete')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert str(form.render()).count('<option') == 3
    assert 'data-autocomplete' in str(form.render())


def test_item_get_display_value(pub):
    field = fields.ItemField(id='1', label='Foobar', items=['aa', 'ab', 'ac'], display_mode='autocomplete')
    assert field.get_display_value('aa') == 'aa'

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    field.data_source = {'type': 'foobar'}

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]},
        )
        assert field.get_display_value('aa') is None  # no lookup on ?id=

        # no crash if there's no session
        pub.get_request().session = None
        assert field.get_display_value('aa') is None

        # with a url handling ?id
        pub.get_request().datasources_cache = {}
        rsps.get('http://remote.example.net/json?id=aa', json={'data': [{'id': 'aa', 'text': 'foo'}]})
        assert field.get_display_value('aa') == 'foo'

        # with numeric id
        pub.get_request().datasources_cache = {}
        rsps.get('http://remote.example.net/json?id=1', json={'data': [{'id': 1, 'text': 'foo'}]})
        assert field.get_display_value(1) == 'foo'

        # with None -> not a valid value
        pub.get_request().datasources_cache = {}
        assert field.get_display_value(None) is None


def test_item_render_as_list_with_hint(pub):
    items = ['aa', 'ab', 'ac']
    field = fields.ItemField(id='1', label='Foobar', display_mode='list', items=items)
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert len(PyQuery(str(form.render())).find('.hint')) == 0
    assert len(PyQuery(str(form.render())).find('option')) == 3

    field = fields.ItemField(id='1', label='Foobar', display_mode='list', items=items, hint='This is an hint')
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert len(PyQuery(str(form.render())).find('.hint')) == 0
    assert len(PyQuery(str(form.render())).find('option')) == 4

    field = fields.ItemField(
        id='1', label='Foobar', display_mode='list', items=items, hint='This is a very long hint' + 'x ' * 50
    )
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert len(PyQuery(str(form.render())).find('.hint')) == 1
    assert len(PyQuery(str(form.render())).find('option')) == 3

    field = fields.ItemField(
        id='1',
        label='Foobar',
        display_mode='list',
        items=items,
        hint='This is an hint',
        use_hint_as_first_option=False,
    )
    form = Form(use_tokens=False)
    field.add_to_form(form)
    assert len(PyQuery(str(form.render())).find('.hint')) == 1
    assert len(PyQuery(str(form.render())).find('option')) == 3


def test_item_render_as_radio(pub):
    items_kwargs = []
    items_kwargs.append({'items': ['aa', 'ab', 'ac']})
    items_kwargs.append(
        {
            'data_source': {
                'type': 'jsonvalue',
                'value': '[{"id": "aa", "text": "aa"}, {"id": "ab", "text": "ab"}, {"id": "ac", "text": "ac"}]',
            }
        }
    )

    for item_kwargs in items_kwargs:
        field = fields.ItemField(id='1', label='Foobar', display_mode='radio', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 3

        field = fields.ItemField(
            id='1', label='Foobar', required='optional', display_mode='radio', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 3

        field = fields.ItemField(
            id='1',
            label='Foobar',
            display_mode='radio',
            required='optional',
            hint='Bla bla bla',
            **item_kwargs,
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 3

        field = fields.ItemField(
            id='1',
            label='Foobar',
            display_mode='radio',
            required='required',
            hint='Bla bla bla',
            **item_kwargs,
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 3

    items_kwargs = []
    items_kwargs.append({'items': None})
    items_kwargs.append({'items': []})
    items_kwargs.append({'data_source': {'type': 'jsonvalue', 'value': '[]'}})
    for item_kwargs in items_kwargs:
        field = fields.ItemField(id='1', label='Foobar', display_mode='radio', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 1

        field = fields.ItemField(
            id='1', label='Foobar', required='optional', display_mode='radio', **item_kwargs
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 1

        field = fields.ItemField(
            id='1',
            label='Foobar',
            display_mode='radio',
            required='optional',
            hint='Bla bla bla',
            **item_kwargs,
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 1

        field = fields.ItemField(
            id='1',
            label='Foobar',
            display_mode='radio',
            required='required',
            hint='Bla bla bla',
            **item_kwargs,
        )
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('"radio"') == 1


def test_item_radio_orientation(pub):
    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa', 'ab', 'ac'])
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' in str(form.widgets[-1].render())
    assert 'widget-radio-orientation-auto' in str(form.widgets[-1].render())

    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa' * 30, 'ab', 'ac'])
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' not in str(form.widgets[-1].render())

    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa', 'ab' * 30, 'ac'])
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' not in str(form.widgets[-1].render())

    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa', 'ab', 'ac', 'ad'])
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' in str(form.widgets[-1].render())

    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa', 'ab' * 30, 'ac'])
    field.radio_orientation = 'horizontal'
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' in str(form.widgets[-1].render())
    assert 'widget-radio-orientation-horizontal' in str(form.widgets[-1].render())

    field = fields.ItemField(id='1', label='Foobar', display_mode='radio', items=['aa', 'ab', 'ac'])
    field.radio_orientation = 'vertical'
    form = Form(use_tokens=False)
    field.add_to_form(form)
    form.render()
    assert 'widget-inline-radio' not in str(form.widgets[-1].render())
    assert 'widget-radio-orientation-vertical' in str(form.widgets[-1].render())


def test_item_migrate(pub):
    field = fields.ItemField()
    assert field.radio_orientation == 'auto'
    assert not field.migrate()

    field.extra_css_class = 'blah widget-inline-radio'
    assert field.migrate()
    assert field.radio_orientation == 'horizontal'
    assert field.extra_css_class == 'blah'


def test_items_render(pub):
    items_kwargs = []
    items_kwargs.append({'items': ['aa', 'ab', 'ac']})
    items_kwargs.append(
        {
            'data_source': {
                'type': 'jsonvalue',
                'value': '[{"id": "aa", "text": "aa"}, {"id": "ab", "text": "ab"}, {"id": "ac", "text": "ac"}]',
            }
        }
    )

    for item_kwargs in items_kwargs:
        field = fields.ItemsField(id='1', label='Foobar', **item_kwargs)
        form = Form(use_tokens=False)
        field.add_to_form(form)
        assert str(form.render()).count('type="checkbox"') == 3
        assert '>aa<' in str(form.render())
        assert '>ab<' in str(form.render())
        assert '>ac<' in str(form.render())


def test_ranked_items():
    field = fields.RankedItemsField(id='1', label='Foobar', items=['aa', 'ab', 'ac'])
    assert len(field.get_csv_heading()) == 3
    assert field.get_csv_value({'aa': 2, 'ab': 1, 'ac': 3}) == ['ab', 'aa', 'ac']


def test_table_rows():
    field = fields.TableRowsField(id='1', label='Foobar', columns=['aa', 'ab', 'ac'], total_row=False)
    html_table = str(field.get_view_value([['A', 'B', 'C'], ['D', 'E', 'F']]))
    assert html_table.count('<tr>') == 3
    assert html_table.count('<th>') == 3
    assert html_table.count('<td>') == 6
    assert html_table.count('<tfoot>') == 0
    for letter in 'ABCDEF':
        assert '>%s<' % letter in html_table

    rst_table = field.get_rst_view_value([['A', 'B', 'C'], ['D', 'E', 'F']])
    assert rst_table.count('==') == 9

    # check it doesn't crash when new columns are defined
    html_table = str(field.get_view_value([['A', 'B'], ['D', 'E']]))
    assert html_table.count('<tr>') == 3
    assert html_table.count('<th>') == 3
    assert html_table.count('<td>') == 6
    for letter in 'ABDE':
        assert '>%s<' % letter in html_table
    assert html_table.count('<td></td>') == 2

    rst_table = field.get_rst_view_value([['A', 'B'], ['D', 'E']])
    assert rst_table.count('==') == 9
    assert 'A  B  -' in rst_table
    assert 'D  E  -' in rst_table

    # check total rows
    field = fields.TableRowsField(id='1', label='Foobar', columns=['aa', 'ab', 'ac'], total_row=True)
    html_table = str(field.get_view_value([['A', 'B', '10'], ['D', 'E', '20']]))
    assert html_table.count('<tr>') == 4
    assert html_table.count('<th>') == 3
    assert html_table.count('<td>') == 9
    assert html_table.count('<tfoot>') == 1
    assert '<td>30.00</td>' in html_table


def test_date(pub):
    assert fields.DateField().convert_value_from_str('2015-01-04') is not None
    assert fields.DateField().convert_value_from_str('04/01/2015') is not None
    assert fields.DateField().convert_value_from_str('') is None
    assert fields.DateField().convert_value_from_str('not a date') is None


def test_date_anonymise(pub):
    formdef = FormDef()
    formdef.name = 'title'
    formdef.fields = [fields.DateField(id='0', label='date')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': time.strptime('2023-03-28', '%Y-%m-%d')}
    formdata.anonymise()
    assert not formdata.data.get('0')

    formdef.fields[0].anonymise = 'no'
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': time.strptime('2023-03-28', '%Y-%m-%d')}
    formdata.anonymise()
    assert formdata.data.get('0') == time.strptime('2023-03-28', '%Y-%m-%d')


def test_file_convert_from_anything(pub):
    assert fields.FileField().convert_value_from_anything(None) is None

    value = fields.FileField().convert_value_from_anything({'content': 'hello', 'filename': 'test.txt'})
    assert value.base_filename == 'test.txt'
    assert value.get_content() == b'hello'

    value = fields.FileField().convert_value_from_anything(
        {'b64_content': 'aGVsbG8=', 'filename': 'test.txt'}
    )
    assert value.base_filename == 'test.txt'
    assert value.get_content() == b'hello'

    formdef = FormDef()
    formdef.name = 'foobarlazy'
    formdef.fields = [fields.FileField(id='5', label='file', varname='filefield')]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '5': PicklableUpload('test.txt', 'text/plain'),
    }
    formdata.data['5'].receive([b'hello'])
    formdata.store()

    value = fields.FileField().convert_value_from_anything(formdef.data_class().get(formdata.id).data['5'])
    assert value.base_filename == 'test.txt'
    assert value.get_content() == b'hello'

    value = fields.FileField().convert_value_from_anything(
        LazyFormData(formdef.data_class().get(formdata.id)).var.filefield
    )
    assert value.base_filename == 'test.txt'
    assert value.get_content() == b'hello'


def test_file_from_json_value(pub):
    value = fields.FileField().from_json_value({'content': 'aGVsbG8=', 'filename': 'test.txt'})
    assert value.base_filename == 'test.txt'
    assert value.get_content() == b'hello'

    value = fields.FileField().from_json_value(
        {'content': 'aGVsbG8', 'filename': 'test.txt'}  # invalid padding
    )
    assert value is None


def test_new_field_type_options(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disabled-fields', '')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    assert fields.get_field_options(blacklisted_types=[]) == [
        OptGroup('Data'),
        ('bool', 'Check Box (single choice)', 'bool'),
        ('computed', 'Computed Data', 'computed'),
        ('date', 'Date', 'date'),
        ('email', 'Email', 'email'),
        ('file', 'File Upload', 'file'),
        ('item', 'List', 'item'),
        ('text', 'Long Text', 'text'),
        ('map', 'Map', 'map'),
        ('items', 'Multiple choice list', 'items'),
        ('numeric', 'Numeric', 'numeric'),
        ('password', 'Password', 'password'),
        ('ranked-items', 'Ranked Items', 'ranked-items'),
        ('table', 'Table', 'table'),
        ('table-select', 'Table of Lists', 'table-select'),
        ('tablerows', 'Table with rows', 'tablerows'),
        ('string', 'Text (line)', 'string'),
        OptGroup('Display'),
        ('page', 'Page', 'page'),
        ('title', 'Title', 'title'),
        ('subtitle', 'Subtitle', 'subtitle'),
        ('comment', 'Comment', 'comment'),
        OptGroup('Agendas'),
        ('time-range', 'Time range', 'time-range'),
    ]
    assert fields.get_field_options(blacklisted_types=['password', 'page']) == [
        OptGroup('Data'),
        ('bool', 'Check Box (single choice)', 'bool'),
        ('computed', 'Computed Data', 'computed'),
        ('date', 'Date', 'date'),
        ('email', 'Email', 'email'),
        ('file', 'File Upload', 'file'),
        ('item', 'List', 'item'),
        ('text', 'Long Text', 'text'),
        ('map', 'Map', 'map'),
        ('items', 'Multiple choice list', 'items'),
        ('numeric', 'Numeric', 'numeric'),
        ('ranked-items', 'Ranked Items', 'ranked-items'),
        ('table', 'Table', 'table'),
        ('table-select', 'Table of Lists', 'table-select'),
        ('tablerows', 'Table with rows', 'tablerows'),
        ('string', 'Text (line)', 'string'),
        OptGroup('Display'),
        ('title', 'Title', 'title'),
        ('subtitle', 'Subtitle', 'subtitle'),
        ('comment', 'Comment', 'comment'),
        OptGroup('Agendas'),
        ('time-range', 'Time range', 'time-range'),
    ]

    pub.site_options.set('options', 'disabled-fields', 'table, password, ')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    assert fields.get_field_options(blacklisted_types=[]) == [
        OptGroup('Data'),
        ('bool', 'Check Box (single choice)', 'bool'),
        ('computed', 'Computed Data', 'computed'),
        ('date', 'Date', 'date'),
        ('email', 'Email', 'email'),
        ('file', 'File Upload', 'file'),
        ('item', 'List', 'item'),
        ('text', 'Long Text', 'text'),
        ('map', 'Map', 'map'),
        ('items', 'Multiple choice list', 'items'),
        ('numeric', 'Numeric', 'numeric'),
        ('ranked-items', 'Ranked Items', 'ranked-items'),
        ('table-select', 'Table of Lists', 'table-select'),
        ('tablerows', 'Table with rows', 'tablerows'),
        ('string', 'Text (line)', 'string'),
        OptGroup('Display'),
        ('page', 'Page', 'page'),
        ('title', 'Title', 'title'),
        ('subtitle', 'Subtitle', 'subtitle'),
        ('comment', 'Comment', 'comment'),
        OptGroup('Agendas'),
        ('time-range', 'Time range', 'time-range'),
    ]
    assert fields.get_field_options(blacklisted_types=['password', 'page']) == [
        OptGroup('Data'),
        ('bool', 'Check Box (single choice)', 'bool'),
        ('computed', 'Computed Data', 'computed'),
        ('date', 'Date', 'date'),
        ('email', 'Email', 'email'),
        ('file', 'File Upload', 'file'),
        ('item', 'List', 'item'),
        ('text', 'Long Text', 'text'),
        ('map', 'Map', 'map'),
        ('items', 'Multiple choice list', 'items'),
        ('numeric', 'Numeric', 'numeric'),
        ('ranked-items', 'Ranked Items', 'ranked-items'),
        ('table-select', 'Table of Lists', 'table-select'),
        ('tablerows', 'Table with rows', 'tablerows'),
        ('string', 'Text (line)', 'string'),
        OptGroup('Display'),
        ('title', 'Title', 'title'),
        ('subtitle', 'Subtitle', 'subtitle'),
        ('comment', 'Comment', 'comment'),
        OptGroup('Agendas'),
        ('time-range', 'Time range', 'time-range'),
    ]


def test_block_do_not_pickle_cache(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', hint='hintblock'),
    ]
    formdef.store()

    assert formdef.fields[0]._block is None
    assert formdef.fields[0].block is not None  # will cache the value
    assert formdef.fields[0]._block is not None

    formdef.store()

    formdef = FormDef.get(formdef.id)
    assert formdef.fields[0]._block is None


def test_block_migrate(pub):
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', type='block:foobar', hint='hintblock'),
    ]
    formdef.store()

    formdef = FormDef.get(formdef.id)
    assert formdef.fields[0].key == 'block'
    assert formdef.fields[0].block_slug == 'foobar'
