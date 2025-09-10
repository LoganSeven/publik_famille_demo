import io

import pytest
from quixote import cleanup

from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)

from .utilities import clean_temporary_pub, create_temporary_pub

category_classes = [
    Category,
    CardDefCategory,
    BlockCategory,
    WorkflowCategory,
    MailTemplateCategory,
    CommentTemplateCategory,
    DataSourceCategory,
]


def setup_module(module):
    cleanup()

    global pub

    pub = create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


@pytest.mark.parametrize('category_class', category_classes)
def test_store(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test2 = category_class.get(test.id)
    assert test.id == test2.id
    assert test.name == test2.name
    assert test.description == test2.description


@pytest.mark.parametrize('category_class', category_classes)
def test_urlname(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)
    assert test.url_name == 'test'


@pytest.mark.parametrize('category_class', category_classes)
def test_duplicate_urlname(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.store()
    test.refresh_from_storage()
    assert test.url_name == 'test'

    test2 = category_class()
    test2.name = 'Test'
    test2.store()
    test2.refresh_from_storage()
    assert test2.url_name == 'test-2'


@pytest.mark.parametrize('category_class', category_classes)
def test_name_giving_a_forbidden_slug(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'API'
    test.store()
    test.refresh_from_storage()
    assert test.url_name == 'cat-api'


@pytest.mark.parametrize('category_class', category_classes)
def test_sort_positions(category_class):
    category_class.wipe()

    categories = []
    for i in range(10):
        test = category_class()
        test.name = 'Test %s' % i
        test.position = 10 - i
        categories.append(test)

    # unset some positions, those categories will appear last
    for i in range(8, 10):
        categories[i].position = None

    category_class.sort_by_position(categories)
    assert categories[0].name == 'Test 7'
    assert categories[-1].name in ('Test 8', 'Test 9')


@pytest.mark.parametrize('category_class', category_classes)
def test_xml_export(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)

    assert b'<name>Test</name>' in test.export_to_xml_string(include_id=True)
    assert f' id="{test.id}"'.encode() in test.export_to_xml_string(include_id=True)
    assert f' id="{test.id}"'.encode() not in test.export_to_xml_string(include_id=False)


@pytest.mark.parametrize('category_class', category_classes)
def test_xml_import(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)

    fd = io.BytesIO(test.export_to_xml_string(include_id=True))
    test2 = category_class.import_from_xml(fd, include_id=True)
    assert str(test.id) == str(test2.id)
    assert test.name == test2.name
    assert test.description == test2.description


@pytest.mark.parametrize('category_class', category_classes)
def test_get_by_urlname(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)
    test2 = category_class.get_by_urlname('test')
    assert test.id == test2.id


@pytest.mark.parametrize('category_class', category_classes)
def test_has_urlname(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)

    assert category_class.has_urlname('test')
    assert not category_class.has_urlname('foobar')


@pytest.mark.parametrize('category_class', category_classes)
def test_remove_self(category_class):
    category_class.wipe()
    test = category_class()
    test.name = 'Test'
    test.description = 'Hello world'
    test.store()
    test = category_class.get(test.id)
    test.remove_self()

    with pytest.raises(KeyError):
        category_class.get(test.id)
