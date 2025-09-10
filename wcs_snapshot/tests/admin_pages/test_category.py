import io
import xml.etree.ElementTree as ET

import pytest
from webtest import Upload

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

    create_superuser(pub)

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_categories(pub):
    app = login(get_app(pub))
    app.get('/backoffice/forms/categories/')


def test_categories_legacy_urls(pub):
    app = login(get_app(pub))
    resp = app.get('/backoffice/categories/')
    assert resp.location.endswith('/backoffice/forms/categories/')
    resp = app.get('/backoffice/categories/1')
    assert resp.location.endswith('/backoffice/forms/categories/1')
    resp = app.get('/backoffice/categories/1/')
    assert resp.location.endswith('/backoffice/forms/categories/1/')


def test_categories_new(pub):
    Category.wipe()
    app = login(get_app(pub))

    # go to the page and cancel
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click('New Category')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/forms/categories/'

    # go to the page and add a category
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a new category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/categories/'
    resp = resp.follow()
    assert 'a new category' in resp.text
    resp = resp.click('a new category')
    assert resp.pyquery('#appbar h2').text() == 'a new category'

    category = Category.select()[0]
    assert Category.get(category.id).name == 'a new category'
    assert Category.get(category.id).description == 'description of the category'


def test_categories_edit(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    assert 'Identifier: foobar' in resp.text
    assert 'No form associated to this category' in resp.text

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['description'] = 'category description'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/categories/'
    resp = resp.follow()
    resp = resp.click('foobar')
    assert resp.pyquery('#appbar h2').text() == 'foobar'

    assert Category.get(category.id).description == 'category description'

    app.get('/backoffice/forms/categories/foo-bar/', status=404)


def test_categories_edit_duplicate_name(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()
    category2 = Category(name='foobar2')
    category2.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/')

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['name'] = 'foobar2'
    resp = resp.forms[0].submit('submit')
    assert 'This name is already used' in resp.text

    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/forms/categories/'


def test_categories_with_formdefs(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()

    FormDef.wipe()
    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    assert 'form bar' not in resp.text

    formdef = FormDef()
    formdef.name = 'form bar'
    formdef.fields = []
    formdef.category_id = category.id
    formdef.store()

    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    assert 'form bar' in resp.text
    assert 'No form associated to this category' not in resp.text


def test_categories_delete(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()

    FormDef.wipe()
    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    assert 'popup.js' in resp.text

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == f'http://example.net/backoffice/forms/categories/{category.id}/'
    assert Category.count() == 1

    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/categories/'
    resp = resp.follow()
    assert Category.count() == 0


def test_categories_edit_description(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.description = 'category description'
    category.store()

    app = login(get_app(pub))
    # this URL is used for editing from the frontoffice, there's no link
    # pointing to it in the admin.
    resp = app.get(f'/backoffice/forms/categories/{category.id}/description')
    assert resp.forms[0]['description'].value == 'category description'
    resp.forms[0]['description'] = 'updated description'

    # check cancel doesn't save the change
    resp2 = resp.forms[0].submit('cancel')
    assert resp2.location == f'http://example.net/backoffice/forms/categories/{category.id}/'
    assert Category.get(category.id).description == 'category description'

    # check submit does it properly
    resp2 = resp.forms[0].submit('submit')
    assert resp2.location == f'http://example.net/backoffice/forms/categories/{category.id}/'
    resp2 = resp2.follow()
    assert Category.get(category.id).description == 'updated description'


def test_categories_new_duplicate_name(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert 'This name is already used' in resp.text


def test_categories_reorder(pub):
    Category.wipe()
    category1 = Category(name='foo')
    category1.store()
    category2 = Category(name='bar')
    category2.store()
    category3 = Category(name='baz')
    category3.store()

    app = login(get_app(pub))
    app.get(f'/backoffice/forms/categories/update_order?order={category1.id};{category2.id};{category3.id};')
    categories = Category.select()
    Category.sort_by_position(categories)
    assert [x.id for x in categories] == [category1.id, category2.id, category3.id]

    app.get(f'/backoffice/forms/categories/update_order?order={category3.id};{category1.id};{category2.id};0')
    categories = Category.select()
    Category.sort_by_position(categories)
    assert [x.id for x in categories] == [category3.id, category1.id, category2.id]


def test_categories_edit_roles(pub):
    pub.role_class.wipe()
    role_a = pub.role_class(name='a')
    role_a.store()
    role_b = pub.role_class(name='b')
    role_b.store()

    Category.wipe()
    category = Category(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/edit')

    resp.form['export_roles$element0'] = role_a.id
    resp = resp.form.submit('export_roles$add_element')
    resp.form['export_roles$element1'] = role_b.id

    resp.form['statistics_roles$element0'] = role_a.id
    resp = resp.form.submit('submit')

    category = Category.get(category.id)
    assert {x.id for x in category.export_roles} == {role_a.id, role_b.id}
    assert {x.id for x in category.statistics_roles} == {role_a.id}

    resp = app.get(f'/backoffice/forms/categories/{category.id}/edit')
    assert resp.form['export_roles$element0'].value == role_a.id


def test_categories_export(pub):
    Category.wipe()
    category = Category(name='foobar')
    category.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/categories/{category.id}/')
    resp = resp.click('Export')
    xml_export = resp.text

    xml_export_fd = io.StringIO(xml_export)
    imported_category = Category.import_from_xml(xml_export_fd)
    assert imported_category.name == category.name


def test_categories_import(pub):
    app = login(get_app(pub))

    Category.wipe()
    category = Category(name='foobar')
    category.store()
    category_xml = ET.tostring(category.export_to_xml(include_id=True))
    Category.wipe()
    CardDefCategory.wipe()

    # import to wrong category kind
    resp = app.get('/backoffice/cards/categories/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('category.wcs', category_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File' in resp.text
    assert Category.count() == 0
    assert CardDefCategory.count() == 0

    # successful import
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('category.wcs', category_xml)
    resp = resp.forms[0].submit()
    assert Category.count() == 1
    assert {x.slug for x in Category.select()} == {'foobar'}

    # repeat import -> slug change
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('category.wcs', category_xml)
    resp = resp.forms[0].submit()
    assert Category.count() == 2
    assert {x.slug for x in Category.select()} == {'foobar', 'foobar-2'}

    # cancel
    resp = app.get('/backoffice/forms/categories/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('category.wcs', category_xml)
    resp = resp.forms[0].submit('cancel')
    assert Category.count() == 2


@pytest.mark.parametrize('klass', [Category, CardDefCategory])
def test_category_by_slug(pub, klass):
    klass.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    cat = klass()
    cat.name = 'cat title'
    cat.store()

    assert app.get(f'/backoffice/{cat.backoffice_base_url}by-slug/cat-title').location == cat.get_admin_url()
    assert app.get(f'/backoffice/{cat.backoffice_base_url}by-slug/xxx', status=404)
