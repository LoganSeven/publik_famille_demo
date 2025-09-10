import pytest

from wcs.qommon.ezt import EZTException
from wcs.qommon.misc import get_variadic_url

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_url_unchanged(pub):
    assert get_variadic_url('http://www.example.net/foobar', {}) == 'http://www.example.net/foobar'


def test_url_scheme(pub):
    assert (
        get_variadic_url('http[https]://www.example.net/foobar', {'https': 's'})
        == 'https://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http[https]://www.example.net/foobar', {'https': ''})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http[https]://www.example.net/foobar/', {'https': ''})
        == 'http://www.example.net/foobar/'
    )
    assert (
        get_variadic_url('http[https]://www.example.net/foo/bar/', {'https': ''})
        == 'http://www.example.net/foo/bar/'
    )
    assert (
        get_variadic_url('http[https]://www.example.net/foo/bar', {'https': ''})
        == 'http://www.example.net/foo/bar'
    )

    assert (
        get_variadic_url('http{{ https }}://www.example.net/foobar', {'https': 's'})
        == 'https://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http{{ https }}://www.example.net/foobar', {'https': ''})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http{{ https }}://www.example.net/foobar/', {'https': ''})
        == 'http://www.example.net/foobar/'
    )
    assert (
        get_variadic_url('http{{ https }}://www.example.net/foo/bar/', {'https': ''})
        == 'http://www.example.net/foo/bar/'
    )
    assert (
        get_variadic_url('http{{ https }}://www.example.net/foo/bar', {'https': ''})
        == 'http://www.example.net/foo/bar'
    )


def test_url_netloc(pub):
    assert (
        get_variadic_url('http://[hostname]/foobar', {'hostname': 'www.example.net'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http://[hostname]/foobar', {'hostname': 'www.example.com'})
        == 'http://www.example.com/foobar'
    )

    assert (
        get_variadic_url('http://{{ hostname }}/foobar', {'hostname': 'www.example.net'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http://{{ hostname }}/foobar', {'hostname': 'www.example.com'})
        == 'http://www.example.com/foobar'
    )


def test_url_netloc_port(pub):
    assert (
        get_variadic_url('http://www.example.net:[port]/foobar', {'port': '80'})
        == 'http://www.example.net:80/foobar'
    )
    assert (
        get_variadic_url('http://www.example.net:{{ port }}/foobar', {'port': '80'})
        == 'http://www.example.net:80/foobar'
    )


def test_url_path(pub):
    assert (
        get_variadic_url('http://www.example.net/[path]', {'path': 'foobar'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http://www.example.net/[path]', {'path': 'foo bar'})
        == 'http://www.example.net/foo%20bar'
    )

    assert (
        get_variadic_url('http://www.example.net/{{ path }}', {'path': 'foobar'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('http://www.example.net/{{ path }}', {'path': 'foo bar'})
        == 'http://www.example.net/foo bar'
    )
    assert (
        get_variadic_url('http://www.example.net/{{ path|urlencode }}', {'path': 'foo bar'})
        == 'http://www.example.net/foo%20bar'
    )


def test_url_query_variable(pub):
    assert (
        get_variadic_url('http://www.example.net/foobar?hello=[world]', {'world': 'world'})
        == 'http://www.example.net/foobar?hello=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello=[world]', {'world': 'a b'})
        == 'http://www.example.net/foobar?hello=a+b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello=[world]', {'world': 'a&b'})
        == 'http://www.example.net/foobar?hello=a%26b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello=[world]', {'world': 'a=b'})
        == 'http://www.example.net/foobar?hello=a%3Db'
    )

    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world }}', {'world': 'world'})
        == 'http://www.example.net/foobar?hello=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world }}', {'world': 'a b'})
        == 'http://www.example.net/foobar?hello=a b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world|urlencode }}', {'world': 'a b'})
        == 'http://www.example.net/foobar?hello=a%20b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world }}', {'world': 'a&b'})
        == 'http://www.example.net/foobar?hello=a&b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world|urlencode }}', {'world': 'a&b'})
        == 'http://www.example.net/foobar?hello=a%26b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world }}', {'world': 'a=b'})
        == 'http://www.example.net/foobar?hello=a=b'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?hello={{ world|urlencode }}', {'world': 'a=b'})
        == 'http://www.example.net/foobar?hello=a%3Db'
    )


def test_url_query_key(pub):
    assert (
        get_variadic_url('http://www.example.net/foobar?[hello]=world', {'hello': 'hello'})
        == 'http://www.example.net/foobar?hello=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?[hello]=world', {'hello': 'a b'})
        == 'http://www.example.net/foobar?a+b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?[hello]=world', {'hello': 'a&b'})
        == 'http://www.example.net/foobar?a%26b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?[hello]=world', {'hello': 'a=b'})
        == 'http://www.example.net/foobar?a%3Db=world'
    )

    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello }}=world', {'hello': 'hello'})
        == 'http://www.example.net/foobar?hello=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello }}=world', {'hello': 'a b'})
        == 'http://www.example.net/foobar?a b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello|urlencode }}=world', {'hello': 'a b'})
        == 'http://www.example.net/foobar?a%20b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello }}=world', {'hello': 'a&b'})
        == 'http://www.example.net/foobar?a&b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello|urlencode }}=world', {'hello': 'a&b'})
        == 'http://www.example.net/foobar?a%26b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello }}=world', {'hello': 'a=b'})
        == 'http://www.example.net/foobar?a=b=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello|urlencode }}=world', {'hello': 'a=b'})
        == 'http://www.example.net/foobar?a%3Db=world'
    )


def test_url_query_whole(pub):
    assert (
        get_variadic_url('http://www.example.net/foobar?[hello]', {'hello': 'hello=world'})
        == 'http://www.example.net/foobar?hello=world'
    )

    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello }}', {'hello': 'hello=world'})
        == 'http://www.example.net/foobar?hello=world'
    )
    assert (
        get_variadic_url('http://www.example.net/foobar?{{ hello|urlencode }}', {'hello': 'hello=world'})
        == 'http://www.example.net/foobar?hello%3Dworld'
    )


def test_url_netloc_port_and_path(pub):
    assert (
        get_variadic_url('http://www.example.net:[port]/foobar/[path]', {'port': '80', 'path': 'baz'})
        == 'http://www.example.net:80/foobar/baz'
    )
    assert (
        get_variadic_url('http://www.example.net:[port]/foobar/[path]', {'port': '80', 'path': 'b z'})
        == 'http://www.example.net:80/foobar/b%20z'
    )

    assert (
        get_variadic_url('http://www.example.net:{{ port }}/foobar/{{ path }}', {'port': '80', 'path': 'baz'})
        == 'http://www.example.net:80/foobar/baz'
    )
    assert (
        get_variadic_url('http://www.example.net:{{ port }}/foobar/{{ path }}', {'port': '80', 'path': 'b z'})
        == 'http://www.example.net:80/foobar/b z'
    )
    assert (
        get_variadic_url(
            'http://www.example.net:{{ port|urlencode }}/foobar/{{ path|urlencode }}',
            {'port': '80', 'path': 'b z'},
        )
        == 'http://www.example.net:80/foobar/b%20z'
    )


def test_url_whole(pub):
    assert (
        get_variadic_url('[url]', {'url': 'http://www.example.net/foobar'}) == 'http://www.example.net/foobar'
    )

    assert (
        get_variadic_url('{{ url }}', {'url': 'http://www.example.net/foobar'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url('{{ url }}', {'url': 'http://www.example.net/?foo=bar&bar=foo'})
        == 'http://www.example.net/?foo=bar&bar=foo'
    )


def test_url_server(pub):
    for url in ('http://www.example.net', 'http://www.example.net/'):
        assert get_variadic_url('[url]/foobar', {'url': url}) == 'http://www.example.net/foobar'
        assert get_variadic_url('[url]/foobar/', {'url': url}) == 'http://www.example.net/foobar/'
        assert get_variadic_url('{{url}}/foobar/', {'url': url}) == 'http://www.example.net/foobar/'
    assert (
        get_variadic_url('[url]foo/bar/', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foo/bar/'
    )
    assert (
        get_variadic_url('[url]foobar/', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/'
    )
    assert (
        get_variadic_url('[url]foo/bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foo/bar'
    )

    assert (
        get_variadic_url('{{ url }}/foobar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar'
    )
    assert (
        get_variadic_url(
            '{{ url }}/foobar', {'url': 'http://www.example.net/'}  # Django is more conservative here:
        )
        == 'http://www.example.net/foobar'
    )
    # to be "smart", use Django templates language:
    for url in ('http://www.example.net', 'http://www.example.net/'):
        assert (
            get_variadic_url('{{ url }}{% if url|last != "/" %}/{% endif %}foobar', {'url': url})
            == 'http://www.example.net/foobar'
        )
        assert (
            get_variadic_url('{{ url }}{% if url|last != "/" %}/{% endif %}foobar/', {'url': url})
            == 'http://www.example.net/foobar/'
        )
    assert (
        get_variadic_url('{{ url }}foo/bar/', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foo/bar/'
    )
    assert (
        get_variadic_url('{{ url }}foobar/', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/'
    )
    assert (
        get_variadic_url('{{ url }}foo/bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foo/bar'
    )


def test_url_server_qs(pub):
    assert (
        get_variadic_url('[url]?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/?foo=bar'
    )
    assert (
        get_variadic_url('[url]?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/?foo=bar'
    )
    assert (
        get_variadic_url('[url]/?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/?foo=bar'
    )
    assert (
        get_variadic_url('[url]/?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/?foo=bar'
    )

    # Django is more conservative
    assert (
        get_variadic_url('{{ url }}?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}/?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}/?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/?foo=bar'
    )


def test_url_server_more(pub):
    assert (
        get_variadic_url('[url]/foobar/json?toto', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/json?toto'
    )
    assert (
        get_variadic_url('[url]/foobar/json?toto', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?toto'
    )
    assert (
        get_variadic_url('[url]foobar/json?toto', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?toto'
    )

    # Django is more conservative
    assert (
        get_variadic_url('{{ url }}/foobar/json?toto', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/json?toto'
    )
    assert (
        get_variadic_url('{{ url }}/foobar/json?toto', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?toto'
    )
    assert (
        get_variadic_url('{{ url }}foobar/json?toto', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?toto'
    )


def test_url_server_even_more(pub):
    assert (
        get_variadic_url('[url]/foobar/json?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )
    assert (
        get_variadic_url('[url]/foobar/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )
    assert (
        get_variadic_url('[url]foobar/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )

    # Django is more conservative
    assert (
        get_variadic_url('{{ url }}/foobar/json?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}/foobar/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}foobar/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/json?foo=bar'
    )


def test_url_server_even_more_more(pub):
    assert (
        get_variadic_url('[url]/foobar/baz/json?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/baz/json?foo=bar'
    )
    assert (
        get_variadic_url('[url]/foobar/baz/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/baz/json?foo=bar'
    )

    # Django is more conservative
    assert (
        get_variadic_url('{{ url }}/foobar/baz/json?foo=bar', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/baz/json?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}/foobar/baz/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/baz/json?foo=bar'
    )
    assert (
        get_variadic_url('{{ url }}foobar/baz/json?foo=bar', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/baz/json?foo=bar'
    )


def test_url_whole_with_qs(pub):
    assert (
        get_variadic_url('[url]', {'url': 'http://www.example.net/?foo=bar'})
        == 'http://www.example.net/?foo=bar'
    )

    assert (
        get_variadic_url('{{ url }}', {'url': 'http://www.example.net/?foo=bar'})
        == 'http://www.example.net/?foo=bar'
    )


def test_url_whole_with_qs_2(pub):
    for url in ('[url]?bar=foo', '[url]&bar=foo', '[url]/?bar=foo'):
        assert get_variadic_url(url, {'url': 'http://www.example.net/?foo=bar'}) in (
            'http://www.example.net/?bar=foo&foo=bar',
            'http://www.example.net/?foo=bar&bar=foo',
        )

    assert (
        get_variadic_url('{{ url }}&bar=foo', {'url': 'http://www.example.net/?foo=bar'})
        == 'http://www.example.net/?foo=bar&bar=foo'
    )
    # to be "smart", use Django templates language:
    assert (
        get_variadic_url(
            '{{ url }}{% if "?" in url %}&{% else %}?{% endif %}bar=foo',
            {'url': 'http://www.example.net/?foo=bar'},
        )
        == 'http://www.example.net/?foo=bar&bar=foo'
    )
    assert (
        get_variadic_url(
            '{{ url }}{% if "?" in url %}&{% else %}?{% endif %}bar=foo', {'url': 'http://www.example.net/'}
        )
        == 'http://www.example.net/?bar=foo'
    )


def test_path_missing_var(pub):
    assert (
        get_variadic_url('http://www.example.net/foobar/[path]', {}) == 'http://www.example.net/foobar/[path]'
    )

    # Django is permissive here
    assert (
        get_variadic_url('http://www.example.net/foobar/{{ path }}', {}) == 'http://www.example.net/foobar/'
    )


def test_url_base_and_missing_var(pub):
    assert (
        get_variadic_url('[url]/foobar/[path]', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/[path]'
    )
    assert (
        get_variadic_url('[url]foobar/[path]', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/[path]'
    )

    assert (
        get_variadic_url('{{ url }}/foobar/{{ path }}', {'url': 'http://www.example.net'})
        == 'http://www.example.net/foobar/'
    )
    assert (
        get_variadic_url('{{ url }}foobar/{{ path }}', {'url': 'http://www.example.net/'})
        == 'http://www.example.net/foobar/'
    )


def test_url_bad_syntax(pub):
    with pytest.raises(EZTException):
        get_variadic_url('[if-any form_avr]https://example.net/[foo]/', {'foo': 'bar'})

    # Django TemplateSyntaxError
    assert (
        get_variadic_url('{% if %}https://www/{{ foo }}', {'bar': 'nofoo'}) == '{% if %}https://www/{{ foo }}'
    )
    # Django VariableDoesNotExist
    assert get_variadic_url('{{ foo|default:notexist }}', {}) == '{{ foo|default:notexist }}'
