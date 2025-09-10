import copy
import xml.etree.ElementTree as ET

import pytest

from wcs.blocks import BlockDef
from wcs.categories import BlockCategory
from wcs.fields import BlockField, CommentField, MapField, StringField
from wcs.formdef import FormDef
from wcs.workflows import ContentSnapshotPart
from wcs.wscalls import NamedWsCall

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def export_to_indented_xml(block, include_id=False):
    block_xml = block.export_to_xml(include_id=include_id)
    ET.indent(block_xml)
    return block_xml


def assert_import_export_works(block, include_id=False):
    block2 = BlockDef.import_from_xml_tree(
        ET.fromstring(ET.tostring(block.export_to_xml(include_id))), include_id
    )
    assert ET.tostring(export_to_indented_xml(block)) == ET.tostring(export_to_indented_xml(block2))
    return block2


def test_block(pub):
    block = BlockDef(name='test')
    assert_import_export_works(block, include_id=True)


def test_block_with_category(pub):
    category = BlockCategory(name='test category')
    category.store()

    block = BlockDef(name='test category')
    block.category_id = category.id
    block.store()
    block2 = assert_import_export_works(block, include_id=True)
    assert str(block2.category_id) == str(block.category_id)

    # import with non existing category
    BlockCategory.wipe()
    export = ET.tostring(block.export_to_xml(include_id=True))
    block3 = BlockDef.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert block3.category_id is None


def test_blocks_in_form_details(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block1 = BlockDef(name='test empty block')
    block1.fields = [CommentField(id='1', label='plop')]
    block1.store()

    block2 = BlockDef(name='test empty block')
    block2.fields = [StringField(id='1', label='plop')]
    block2.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        BlockField(id='1', label='test1', block_slug=block1.slug),
        BlockField(id='2', label='test2', block_slug=block2.slug),
    ]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{}],
            'schema': {'1': 'comment'},
        },
        '1_display': 'test empty block',
        '2': {
            'data': [{'1': 'foo'}],
            'schema': {'1': 'string'},
        },
        '2_display': 'bar',
    }
    formdata.just_created()
    formdata.store()

    details = formdef.get_detailed_email_form(formdata, '')
    assert 'test1' not in details
    assert 'test empty block' not in details
    assert 'test2' in details
    assert 'plop:\n  foo\n' in details


def test_block_migrate(pub):
    block = BlockDef(name='test category')
    block.fields = [StringField(id='1', label='plop')]
    block.fields[0].anonymise = True
    block.store()

    block = BlockDef.get(block.id)
    assert block.fields[0].anonymise == 'final'


def test_block_get_dependencies(pub):
    NamedWsCall.wipe()
    wscall = NamedWsCall('hello')
    wscall.store()
    wscall2 = NamedWsCall('world')
    wscall2.store()
    block = BlockDef(name='test deps')
    block.fields = [
        StringField(id='1', label='plop', prefill={'type': 'string', 'value': '{{ webservice.hello }}'})
    ]
    block.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'webservice.world == "test"'},
            'error_message': 'error',
        },
    ]
    assert {x.name for x in block.get_dependencies() if isinstance(x, NamedWsCall)} == {'hello', 'world'}


def test_block_anonymise_parts(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef(name='test category')
    block.fields = [
        StringField(id='1', label='plop1', anonymise='final'),
        StringField(id='2', label='plop2', anonymise='no'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        BlockField(id='1', label='test', block_slug=block.slug, anonymise='no'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'1': 'a', '2': 'A'}, {'1': 'b', '2': 'B'}],
            'schema': {'1': 'string', '2': 'string'},
        },
        '1_display': 'a, b',
    }
    formdata.just_created()
    formdata.store()

    formdata.anonymise()
    assert formdata.data['1']['data'] == [{'1': None, '2': 'A'}, {'1': None, '2': 'B'}]

    # check intermediate store
    block.fields = [
        StringField(id='1', label='plop1', anonymise='intermediate'),
        StringField(id='2', label='plop2', anonymise='no'),
    ]
    block.store()

    formdef.refresh_from_storage()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'1': 'a', '2': 'A'}, {'1': 'b', '2': 'B'}],
            'schema': {'1': 'string', '2': 'string'},
        },
        '1_display': 'a, b',
    }
    formdata.just_created()
    formdata.store()
    old_data = copy.deepcopy(formdata.data)
    formdata.data['1']['data'] = [{'1': 'a2', '2': 'A'}, {'1': 'b2', '2': 'B'}]
    formdata.store()
    ContentSnapshotPart.take(formdata=formdata, old_data=old_data)

    formdata.anonymise(mode='intermediate')
    assert (
        formdata.data['1']['data']
        == formdata.evolution[0].parts[0].new_data['1']['data']
        == formdata.evolution[0].parts[1].new_data['1']['data']
        == formdata.evolution[0].parts[1].old_data['1']['data']
        == [{'1': None, '2': 'A'}, {'1': None, '2': 'B'}]
    )

    # make sure there's no crash on empty values
    formdata = formdef.data_class()()
    formdata.data = {
        '1': None,
        '1_display': None,
    }
    formdata.just_created()
    formdata.store()
    formdata.anonymise(mode='final')


def test_block_map_data(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef(name='test category')
    block.fields = [
        MapField(id='1', label='plop1'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [BlockField(id='1', label='test', block_slug=block.slug)]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'1': '49;6'}],
            'schema': {'1': 'map'},
        },
        '1_display': 'x',
    }
    formdata.just_created()
    formdata.store()

    formdata.refresh_from_storage()
    assert formdata.data['1'] == {'data': [{'1': {'lat': '49', 'lon': '6'}}], 'schema': {'1': 'map'}}
