import pytest

from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_categories(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/cards/categories/')


def test_categories_new(pub):
    create_superuser(pub)
    CardDefCategory.wipe()
    app = login(get_app(pub))

    # go to the page and cancel
    resp = app.get('/backoffice/cards/categories/')
    resp = resp.click('New Category')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/cards/categories/'

    # go to the page and add a category
    resp = app.get('/backoffice/cards/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a new category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/cards/categories/'
    resp = resp.follow()
    assert 'a new category' in resp.text
    resp = resp.click('a new category')
    assert resp.pyquery('#appbar h2').text() == 'a new category'

    assert CardDefCategory.get(1).name == 'a new category'
    assert CardDefCategory.get(1).description == 'description of the category'


def test_categories_edit(pub):
    create_superuser(pub)
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/cards/categories/{category.id}/')
    assert 'No card model associated to this category' in resp.text

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['description'] = 'category description'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/cards/categories/'
    resp = resp.follow()
    resp = resp.click('foobar')
    assert resp.pyquery('#appbar h2').text() == 'foobar'

    category.refresh_from_storage()
    assert category.description == 'category description'


def test_categories_edit_duplicate_name(pub):
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.store()
    category2 = CardDefCategory(name='foobar2')
    category2.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/cards/categories/{category.id}/')

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['name'] = 'foobar2'
    resp = resp.forms[0].submit('submit')
    assert 'This name is already used' in resp.text

    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/cards/categories/'


def test_categories_with_carddefs(pub):
    create_superuser(pub)
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.store()

    CardDef.wipe()
    app = login(get_app(pub))
    resp = app.get(f'/backoffice/cards/categories/{category.id}/')
    assert 'form bar' not in resp.text

    formdef = CardDef()
    formdef.name = 'form bar'
    formdef.fields = []
    formdef.category_id = category.id
    formdef.store()

    resp = app.get(f'/backoffice/cards/categories/{category.id}/')
    assert 'form bar' in resp.text
    assert 'No card model associated to this category' not in resp.text


def test_categories_delete(pub):
    create_superuser(pub)
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.store()

    CardDef.wipe()
    app = login(get_app(pub))
    resp = app.get(f'/backoffice/cards/categories/{category.id}/')

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == f'http://example.net/backoffice/cards/categories/{category.id}/'
    assert CardDefCategory.count() == 1

    carddef = CardDef()
    carddef.name = 'bar'
    carddef.fields = []
    carddef.category_id = category.id
    carddef.store()

    formdef_category = Category(name='blah')
    formdef_category.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.category_id = formdef_category.id
    formdef.store()

    resp = app.get(f'/backoffice/cards/categories/{category.id}/')
    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/cards/categories/'
    resp = resp.follow()
    assert CardDefCategory.count() == 0

    carddef.refresh_from_storage()
    assert carddef.category_id is None

    formdef.refresh_from_storage()
    assert formdef.category_id == str(formdef_category.id)


def test_categories_edit_description(pub):
    create_superuser(pub)
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.description = 'category description'
    category.store()

    app = login(get_app(pub))
    # this URL is used for editing from the frontoffice, there's no link
    # pointing to it in the admin.
    resp = app.get(f'/backoffice/cards/categories/{category.id}/description')
    assert resp.forms[0]['description'].value == 'category description'
    resp.forms[0]['description'] = 'updated description'

    # check cancel doesn't save the change
    resp2 = resp.forms[0].submit('cancel')
    assert resp2.location == f'http://example.net/backoffice/cards/categories/{category.id}/'
    assert CardDefCategory.get(category.id).description == 'category description'

    # check submit does it properly
    resp2 = resp.forms[0].submit('submit')
    assert resp2.location == f'http://example.net/backoffice/cards/categories/{category.id}/'
    resp2 = resp2.follow()
    assert CardDefCategory.get(category.id).description == 'updated description'


def test_categories_new_duplicate_name(pub):
    CardDefCategory.wipe()
    category = CardDefCategory(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert 'This name is already used' in resp.text


def test_categories_reorder(pub):
    create_superuser(pub)

    CardDefCategory.wipe()
    category1 = CardDefCategory(name='foo')
    category1.store()
    category2 = CardDefCategory(name='bar')
    category2.store()
    category3 = CardDefCategory(name='baz')
    category3.store()

    app = login(get_app(pub))
    app.get(f'/backoffice/cards/categories/update_order?order={category1.id};{category2.id};{category3.id};')
    categories = CardDefCategory.select()
    CardDefCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [category1.id, category2.id, category3.id]

    app.get(f'/backoffice/cards/categories/update_order?order={category3.id};{category1.id};{category2.id};0')
    categories = CardDefCategory.select()
    CardDefCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [category3.id, category1.id, category2.id]
