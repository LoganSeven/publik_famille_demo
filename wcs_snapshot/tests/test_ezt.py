import datetime
import io
import os

import pytest
from quixote import cleanup

from wcs.qommon.ezt import (
    ArgCountSyntaxError,
    Template,
    UnclosedBlocksError,
    UnmatchedElseError,
    UnmatchedEndError,
    _re_parse,
)
from wcs.qommon.template import Template as QommonTemplate
from wcs.scripts import ScriptsSubstitutionProxy

from .utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def test_simple_qualifier():
    template = Template()
    template.parse('<p>[foo]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': 'bar'})
    assert output.getvalue() == '<p>bar</p>'


def test_simple_qualifier_missing_variable():
    template = Template()
    template.parse('<p>[foo]</p>')
    output = io.StringIO()
    template.generate(output, {})
    assert output.getvalue() == '<p>[foo]</p>'


def test_if_any():
    template = Template()
    template.parse('<p>[if-any foo]bar[end]</p>')

    # boolean
    output = io.StringIO()
    template.generate(output, {'foo': True})
    assert output.getvalue() == '<p>bar</p>'

    # no value
    output = io.StringIO()
    template.generate(output, {})
    assert output.getvalue() == '<p></p>'

    # defined but evaluating to False
    output = io.StringIO()
    template.generate(output, {'foo': False})
    assert output.getvalue() == '<p>bar</p>'


def test_if_any_else():
    template = Template()
    template.parse('<p>[if-any foo]bar[else]baz[end]</p>')

    output = io.StringIO()
    template.generate(output, {'foo': True})
    assert output.getvalue() == '<p>bar</p>'

    output = io.StringIO()
    template.generate(output, {})
    assert output.getvalue() == '<p>baz</p>'


def test_is():
    template = Template()
    template.parse('<p>[is foo "bar"]bar[end]</p>')

    # no value
    output = io.StringIO()
    template.generate(output, {})
    assert output.getvalue() == '<p></p>'

    # defined but other value
    output = io.StringIO()
    template.generate(output, {'foo': 'baz'})
    assert output.getvalue() == '<p></p>'

    # defined with correct value
    output = io.StringIO()
    template.generate(output, {'foo': 'bar'})
    assert output.getvalue() == '<p>bar</p>'


def test_for():
    template = Template()
    template.parse('<ul>[for item]<li>[item]</li>[end]</ul>')
    output = io.StringIO()
    template.generate(output, {'item': ['a', 'b', 'c']})
    assert output.getvalue() == '<ul><li>a</li><li>b</li><li>c</li></ul>'

    template.parse(
        '<ul>[for item]<li>[if-index item odd]odd [end]'
        '[if-index item even]even [end]'
        '[if-index item first]first [end]'
        '[if-index item last]last [end]'
        '[if-index item 1]idx [end]'
        '[item]</li>[end]</ul>'
    )
    output = io.StringIO()
    template.generate(output, {'item': ['a', 'b', 'c']})
    assert output.getvalue() == '<ul><li>even first a</li><li>odd idx b</li><li>even last c</li></ul>'


def test_callable_qualifier():
    template = Template()
    template.parse('<p>[foo]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': lambda x: x.write('bar')})
    assert output.getvalue() == '<p>bar</p>'


def test_date_qualifier(pub):
    template = Template()
    template.parse('<p>[foo]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': datetime.date(2019, 1, 2)})
    assert output.getvalue() == '<p>2019-01-02</p>'

    with pub.with_language('fr'):
        output = io.StringIO()
        template.generate(output, {'foo': datetime.date(2019, 1, 2)})
        assert output.getvalue() == '<p>02/01/2019</p>'


def test_datetime_qualifier(pub):
    template = Template()
    template.parse('<p>[foo]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': datetime.datetime(2019, 1, 2, 14, 4)})
    assert output.getvalue() == '<p>2019-01-02 14:04</p>'

    with pub.with_language('fr'):
        output = io.StringIO()
        template.generate(output, {'foo': datetime.datetime(2019, 1, 2, 14, 4)})
        assert output.getvalue() == '<p>02/01/2019 14:04</p>'


def test_is_bool():
    template = Template()
    template.parse('<p>[is foo True]hello[end]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': True})
    assert output.getvalue() == '<p>hello</p>'
    output = io.StringIO()
    template.generate(output, {'foo': False})
    assert output.getvalue() == '<p></p>'


def test_unclosed_block():
    template = Template()
    with pytest.raises(UnclosedBlocksError):
        template.parse('<p>[if-any]Test</p>')
    try:
        template.parse('<p>[if-any]Test</p>')
    except UnclosedBlocksError as e:
        assert e.column == 19 and e.line == 0


def test_unmatched_end():
    template = Template()
    with pytest.raises(UnmatchedEndError):
        template.parse('<p>[if foo]Test[end]</p>')
    try:
        template.parse('<p>[if foo]Test[end]</p>')
    except UnmatchedEndError as e:
        assert e.column == 15 and e.line == 0


def test_unmatched_else():
    template = Template()
    with pytest.raises(UnmatchedElseError):
        template.parse('<p>[else]</p>')
    try:
        template.parse('<p>[else]</p>')
    except UnmatchedElseError as e:
        assert e.column == 3 and e.line == 0


def test_missing_is_arg():
    template = Template()
    with pytest.raises(ArgCountSyntaxError):
        template.parse('[is foobar][end]')
    try:
        template.parse('\ntest [is foobar][end]')
    except ArgCountSyntaxError as e:
        assert e.column == 5 and e.line == 1


def test_array_index():
    template = Template()
    template.parse('<p>[foo.0]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': ['bar']})
    assert output.getvalue() == '<p>bar</p>'

    template = Template()
    template.parse('<p>[foo.bar]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': ['bar']})
    assert output.getvalue() == '<p>[foo.bar]</p>'


def test_array_subindex():
    template = Template()
    template.parse('<p>[foo.0.1]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': [['bar', 'baz']]})
    assert output.getvalue() == '<p>baz</p>'


def test_dict_index():
    template = Template()
    template.parse('<p>[foo.a]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': {'a': 'bar'}})
    assert output.getvalue() == '<p>bar</p>'

    template = Template()
    template.parse('<p>[foo.b]</p>')
    output = io.StringIO()
    template.generate(output, {'foo': {'a': 'bar'}})
    assert output.getvalue() == '<p>[foo.b]</p>'


def test_ezt_script(pub):
    os.mkdir(os.path.join(pub.app_dir, 'scripts'))
    with open(os.path.join(pub.app_dir, 'scripts', 'hello_world.py'), 'w') as fd:
        fd.write('''result = "Hello %s" % ("world" if not args else args[0])''')

    vars = {'script': ScriptsSubstitutionProxy()}
    template = Template()
    template.parse('<p>[script.hello_world]</p>')
    output = io.StringIO()
    template.generate(output, vars)
    assert output.getvalue() == '<p>Hello world</p>'

    vars = {'script': ScriptsSubstitutionProxy()}
    template = Template()
    template.parse('<p>[script.hello_world "fred"]</p>')
    output = io.StringIO()
    template.generate(output, vars)
    assert output.getvalue() == '<p>Hello fred</p>'


def test_re_parse():
    assert _re_parse.split('[a]') == ['', 'a', None, '']
    assert _re_parse.split('[a] [b]') == ['', 'a', None, ' ', 'b', None, '']
    assert _re_parse.split('[a c] [b]') == ['', 'a c', None, ' ', 'b', None, '']
    assert _re_parse.split('x [a] y [b] z') == ['x ', 'a', None, ' y ', 'b', None, ' z']
    assert _re_parse.split('[a "b" c "d"]') == ['', 'a "b" c "d"', None, '']
    assert _re_parse.split(r'["a \"b[foo]" c.d f]') == ['', '"a \\"b[foo]" c.d f', None, '']


def test_disable(pub):
    template = QommonTemplate('<p>[foo]</p>')
    assert template.render(context={'foo': 'bar'}) == '<p>bar</p>'

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disable-ezt-support', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    template = QommonTemplate('<p>[foo]</p>')
    assert template.render(context={'foo': 'bar'}) == '<p>[foo]</p>'
