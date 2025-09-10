from quixote import cleanup

from wcs.qommon.admin.texts import TextsDirectory
from wcs.qommon.http_request import HTTPRequest

from .utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()
    global pub
    pub = create_temporary_pub()
    req = HTTPRequest(None, {})
    pub._set_request(req)
    # create an user otherwise the backoffice is open and a button to edit the
    # text gets automatically added.
    user = pub.user_class(name='foo')
    user.store()


def teardown_module(module):
    clean_temporary_pub()


def test_get_html_text_unset():
    TextsDirectory.register('foo', 'Foo')
    assert TextsDirectory.get_html_text('foo') == ''


def test_get_html_text_default():
    TextsDirectory.register('foo2', 'Foo', default='Foo...')
    assert TextsDirectory.get_html_text('foo2') == '<div class="text-foo2"><p>Foo...</p></div>'


def test_get_html_text_set():
    TextsDirectory.register('foo3', 'Foo', default='Foo...')
    pub.cfg['texts'] = {'text-foo3': None}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo3') == '<div class="text-foo3"><p>Foo...</p></div>'
    pub.cfg['texts'] = {'text-foo3': 'Bar...'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo3') == '<div class="text-foo3"><p>Bar...</p></div>'
    pub.cfg['texts'] = {'text-foo3': '<div>Bar...</div>'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo3') == '<div class="text-foo3"><div>Bar...</div></div>'


def test_get_html_subst():
    # test for variable substitution
    TextsDirectory.register('foo4', 'Foo', default='Foo...')
    pub.substitutions.feed(MockSubstitutionVariables())
    pub.cfg['texts'] = {'text-foo4': 'dj{{ bar }}'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo4') == '<div class="text-foo4"><p>djFoobar</p></div>'
    pub.cfg['texts'] = {'text-foo4': 'ezt[bar]'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo4') == '<div class="text-foo4"><p>eztFoobar</p></div>'

    pub.cfg['texts'] = {'text-foo4': 'dj{{ foo }}'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo4') == '<div class="text-foo4"><p>dj1 &lt; 3</p></div>'
    pub.cfg['texts'] = {'text-foo4': 'ezt[foo]'}
    pub.write_cfg()
    assert TextsDirectory.get_html_text('foo4') == '<div class="text-foo4"><p>ezt1 &lt; 3</p></div>'
