import io
import xml.etree.ElementTree as ET

import pytest

from wcs import fields
from wcs.blocks import BlockDef, BlockdefImportError
from wcs.carddef import CardDef

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def export_to_indented_xml(blockdef, include_id=False):
    blockdef_xml = ET.fromstring(ET.tostring(blockdef.export_to_xml(include_id=include_id)))
    ET.indent(blockdef_xml)
    return blockdef_xml


def test_import_root_node_error():
    export = b'<wrong_root_node><name>Name</name></wrong_root_node>'
    with pytest.raises(BlockdefImportError) as excinfo:
        BlockDef.import_from_xml(io.BytesIO(export))
    assert (
        excinfo.value.msg
        == 'Provided XML file is invalid, it starts with a <wrong_root_node> tag instead of <block>'
    )


def test_import_blockdef_multiple_errors(pub):
    BlockDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.store()

    blockdef = BlockDef()
    blockdef.name = 'foo'
    blockdef.fields = [
        fields.StringField(id='1', data_source={'type': 'foobar'}),
        fields.StringField(id='2', data_source={'type': 'carddef:unknown'}),
        fields.StringField(id='3', data_source={'type': 'carddef:foo:unknown'}),
        fields.BoolField(id='4'),
    ]

    export = ET.tostring(export_to_indented_xml(blockdef)).replace(
        b'<type>bool</type>', b'<type>foobaz</type>'
    )
    with pytest.raises(BlockdefImportError) as excinfo:
        BlockDef.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == (
        'Unknown datasources: carddef:foo:unknown, carddef:unknown, foobar; Unknown field types: foobaz'
    )


def test_import_blockdef_post_conditions(pub):
    BlockDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.store()

    blockdef = BlockDef()
    blockdef.name = 'foo'
    blockdef.fields = []
    blockdef.post_conditions = [
        {'condition': {'type': 'django', 'value': 'blah1'}, 'error_message': 'bar1'},
        {'condition': {'type': 'django', 'value': 'blah2'}, 'error_message': 'bar2'},
    ]

    export = ET.tostring(export_to_indented_xml(blockdef))
    blockdef2 = BlockDef.import_from_xml(io.BytesIO(export))
    assert blockdef.post_conditions == blockdef2.post_conditions
