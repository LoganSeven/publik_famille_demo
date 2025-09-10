import decimal
import io
import time
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest
from quixote.http_request import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.formdef_base import FormdefImportError
from wcs.qommon.form import UploadedFile
from wcs.workflows import Workflow, WorkflowVariablesFieldsFormDef

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def export_to_indented_xml(formdef, include_id=False):
    formdef_xml = ET.fromstring(ET.tostring(formdef.export_to_xml(include_id=include_id)))
    ET.indent(formdef_xml)
    return formdef_xml


def assert_compare_formdef(formdef1, formdef2, include_id=False):
    assert ET.tostring(export_to_indented_xml(formdef1, include_id=include_id)) == ET.tostring(
        export_to_indented_xml(formdef2, include_id=include_id)
    )
    assert formdef1.export_to_json(include_id=include_id, indent=2) == formdef2.export_to_json(
        include_id=include_id, indent=2
    )


def assert_xml_import_export_works(formdef, include_id=False):
    formdef_xml = formdef.export_to_xml(include_id=include_id)
    formdef2 = FormDef.import_from_xml_tree(formdef_xml, include_id=include_id)
    assert_compare_formdef(formdef, formdef2, include_id=include_id)
    return formdef2


def assert_json_import_export_works(formdef, include_id=False):
    formdef2 = FormDef.import_from_json(
        io.StringIO(formdef.export_to_json(include_id=include_id)), include_id=include_id
    )
    assert_compare_formdef(formdef, formdef2, include_id=include_id)
    return formdef2


def test_empty(pub):
    formdef = FormDef()
    formdef.name = 'empty'
    assert_xml_import_export_works(formdef)
    assert_json_import_export_works(formdef)


def test_text_attributes(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    f2 = assert_xml_import_export_works(formdef)
    assert f2.url_name == formdef.url_name
    f2 = assert_json_import_export_works(formdef)
    assert f2.url_name == formdef.url_name


def test_empty_description_tag(pub):
    formdef = FormDef()
    formdef.name = 'empty'
    assert_xml_import_export_works(formdef)
    export = ET.tostring(export_to_indented_xml(formdef))
    # add empty description tag
    export = export.replace(b'<name>empty</name>', b'<name>empty</name><description></description>')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert not formdef2.description


def test_empty_display_locations_tag(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.TitleField(label='title', display_locations=None),
        fields.SubtitleField(label='subtitle', display_locations=[]),
        fields.TextField(label='string', display_locations=[]),
    ]

    formdef_xml = formdef.export_to_xml()
    f1 = formdef_xml.findall('fields/field')[0]
    f2 = formdef_xml.findall('fields/field')[1]
    f3 = formdef_xml.findall('fields/field')[2]
    assert '<display_locations />' in str(ET.tostring(f1))
    assert '<display_locations />' in str(ET.tostring(f2))
    assert '<display_locations />' in str(ET.tostring(f3))

    formdef2 = assert_xml_import_export_works(formdef)
    assert formdef2.fields[0].display_locations == []
    assert formdef2.fields[1].display_locations == []
    assert formdef2.fields[2].display_locations == []
    formdef2_xml = formdef2.export_to_xml()
    f1 = formdef2_xml.findall('fields/field')[0]
    f2 = formdef2_xml.findall('fields/field')[1]
    f3 = formdef2_xml.findall('fields/field')[2]
    assert '<display_locations />' in str(ET.tostring(f1))
    assert '<display_locations />' in str(ET.tostring(f2))
    assert '<display_locations />' in str(ET.tostring(f3))


def test_boolean_attributes(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.confirmation = True
    formdef.enable_tracking_codes = True
    f2 = assert_xml_import_export_works(formdef)
    assert f2.enable_tracking_codes == formdef.enable_tracking_codes
    assert f2.confirmation == formdef.confirmation
    f2 = assert_json_import_export_works(formdef)
    assert f2.enable_tracking_codes == formdef.enable_tracking_codes
    assert f2.confirmation == formdef.confirmation


def test_a_field(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [fields.StringField(id=1, label='Bar', size='40')]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    f2 = assert_json_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)


def test_field_with_True_as_label(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.fields = [fields.StringField(id=1, label='True', size='40')]
    f2 = assert_xml_import_export_works(formdef)
    assert f2.fields[0].label == 'True'


def test_more_fields(pub):
    formdef = FormDef()
    formdef.name = 'Blah'
    formdef.fields = [
        fields.TextField(label='Bar', display_mode='pre'),
        fields.EmailField(label='Bar'),
        fields.BoolField(label='Bar'),
        fields.DateField(label='Bar', minimum_date='2014-01-01'),
        fields.ItemField(label='Bar', items=['foo', 'bar', 'baz']),
        fields.NumericField(label='Bar', min_value=Decimal(-12), max_value=Decimal(12)),
        fields.NumericField(label='Bar', min_value=None, max_value=None),
    ]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[2].key == formdef.fields[2].key
    assert f2.fields[3].minimum_date == formdef.fields[3].minimum_date
    assert f2.fields[4].items == formdef.fields[4].items
    assert f2.fields[5].min_value == formdef.fields[5].min_value
    assert f2.fields[5].max_value == formdef.fields[5].max_value
    assert f2.fields[6].min_value == formdef.fields[6].min_value
    assert f2.fields[6].max_value == formdef.fields[6].max_value

    f2 = assert_json_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[2].key == formdef.fields[2].key
    assert f2.fields[3].minimum_date == formdef.fields[3].minimum_date
    assert f2.fields[4].items == formdef.fields[4].items
    assert f2.fields[5].min_value == formdef.fields[5].min_value
    assert f2.fields[5].max_value == formdef.fields[5].max_value
    assert f2.fields[6].min_value == formdef.fields[6].min_value
    assert f2.fields[6].max_value == formdef.fields[6].max_value


def test_item_radio(pub):
    formdef = FormDef()
    formdef.name = 'Blah'
    formdef.fields = [
        fields.ItemField(
            label='Bar',
            items=['foo', 'bar', 'baz'],
            list_mode='radio',
            id='1',
        ),
    ]

    # test new mode
    assert_json_import_export_works(formdef, include_id=True)

    # test conversion of legacy show_as_radio attribute
    formdef_xml = formdef.export_to_xml(include_id=True)
    field = formdef_xml.findall('fields/field')[0]
    ET.SubElement(field, 'show_as_radio').text = 'True'
    field.remove(field.find('display_mode'))
    fd2 = FormDef.import_from_xml_tree(formdef_xml, include_id=True)
    assert fd2.fields[0].display_mode == 'radio'

    # test conversion of legacy show_as_radio attribute
    formdef_xml = formdef.export_to_xml(include_id=True)
    field = formdef_xml.findall('fields/field')[0]
    ET.SubElement(field, 'show_as_radio').text = 'False'
    field.remove(field.find('display_mode'))
    fd2 = FormDef.import_from_xml_tree(formdef_xml, include_id=True)
    assert fd2.fields[0].display_mode == 'list'


def test_include_id(pub):
    formdef = FormDef()
    formdef.name = 'Blah'
    formdef.fields = [
        fields.TextField(label='Bar', display_mode='pre'),
        fields.EmailField(label='Bar'),
        fields.BoolField(label='Bar'),
        fields.DateField(label='Bar', minimum_date='2014-01-01'),
        fields.ItemField(label='Bar', items=['foo', 'bar', 'baz']),
    ]
    for field in formdef.fields:
        field.id = formdef.get_new_field_id()
    formdef.fields[4].id = '10'
    f2 = assert_xml_import_export_works(formdef, include_id=True)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].id == formdef.fields[0].id
    assert f2.fields[4].id == formdef.fields[4].id

    f2 = assert_json_import_export_works(formdef, include_id=True)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].id == formdef.fields[0].id
    assert f2.fields[4].id == formdef.fields[4].id


def test_workflow_options(pub):
    Workflow.wipe()

    wf = Workflow()
    wf.name = 'test workflow'
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(fields.StringField(label='foo', varname='foo'))
    wf.variables_formdef.fields.append(fields.StringField(label='foo2', varname='foo2'))
    wf.store()

    formdef = FormDef()
    formdef.name = 'workflow options'
    formdef.workflow = wf
    formdef.workflow_options = {'foo': 'bar', 'foo2': 'baré'}
    fd2 = assert_xml_import_export_works(formdef)
    assert fd2.workflow_options == formdef.workflow_options
    fd2 = assert_json_import_export_works(formdef)
    assert fd2.workflow_options == formdef.workflow_options


def test_workflow_options_with_no_values(pub):
    Workflow.wipe()

    wf = Workflow()
    wf.name = 'test workflow'
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(fields.StringField(label='foo', varname='foo'))
    wf.variables_formdef.fields.append(fields.StringField(label='foo2', varname='foo2'))
    wf.store()

    formdef = FormDef()
    formdef.workflow = wf
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': None, 'foo2': None}
    fd2 = assert_xml_import_export_works(formdef)
    assert fd2.workflow_options == formdef.workflow_options
    fd2 = assert_json_import_export_works(formdef)
    assert fd2.workflow_options == formdef.workflow_options


def test_workflow_options_with_file(pub):
    Workflow.wipe()

    wf = Workflow()
    wf.name = 'test workflow'
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(fields.FileField(label='foo', varname='foo'))
    wf.store()

    upload = Upload('/foo/bar', content_type='application/vnd.oasis.opendocument.text')
    file_content = b'''PK\x03\x04\x14\x00\x00\x08\x00\x00\'l\x8eG^\xc62\x0c\'\x00'''
    upload.fp = io.BytesIO()
    upload.fp.write(file_content)
    upload.fp.seek(0)
    model_file = UploadedFile(pub.APP_DIR, None, upload)

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow = wf
    formdef.workflow_options = {'foo': model_file}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'].base_filename == fd2.workflow_options['foo'].base_filename
    assert formdef.workflow_options['foo'].get_content() == fd2.workflow_options['foo'].get_content()
    fd2 = assert_json_import_export_works(formdef)
    assert formdef.workflow_options['foo'].base_filename == fd2.workflow_options['foo'].base_filename
    assert formdef.workflow_options['foo'].get_content() == fd2.workflow_options['foo'].get_content()


def test_workflow_options_with_date(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': time.strptime('2014-02-02', '%Y-%m-%d')}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']


def test_workflow_options_with_boolean(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': True, 'bar': False}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']


def test_workflow_options_with_int(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': 123}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']


def test_workflow_options_with_float(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': 123.2}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']


def test_workflow_options_with_decimal(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {'foo': decimal.Decimal('123.2')}
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']


def test_workflow_options_with_list(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_options = {
        'foo': ['a', 'b', 'c'],
        'foo2': [True, False],
        'foo3': [{'id': 1, 'text': 'blah'}],
    }
    fd2 = assert_xml_import_export_works(formdef)
    assert formdef.workflow_options['foo'] == fd2.workflow_options['foo']
    assert formdef.workflow_options['foo2'] == fd2.workflow_options['foo2']
    assert formdef.workflow_options['foo3'] == fd2.workflow_options['foo3']


def test_workflow_reference(pub):
    Workflow.wipe()
    FormDef.wipe()

    wf = Workflow()
    wf.name = 'test workflow'
    wf.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_id = wf.id
    f2 = assert_xml_import_export_works(formdef)
    assert f2.workflow_id == str(formdef.workflow_id)

    f2 = assert_xml_import_export_works(formdef, include_id=True)
    assert f2.workflow_id == str(formdef.workflow_id)

    formdef_xml_with_id = formdef.export_to_xml(include_id=True)

    # check there's no reference to a non-existing workflow
    Workflow.wipe()
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=False).workflow_id is None
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=True).workflow_id is None

    # check an import that is not using id fields will find the workflow by its
    # name
    wf = Workflow()
    wf.id = '2'
    wf.name = 'test workflow'
    wf.store()
    assert str(FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=False).workflow_id) == '2'
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=True).workflow_id is None


def test_category_reference(pub):
    Category.wipe()
    FormDef.wipe()

    cat = Category()
    cat.name = 'test category'
    cat.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.category_id = str(cat.id)
    f2 = assert_xml_import_export_works(formdef)
    assert f2.category_id == formdef.category_id

    f2 = assert_xml_import_export_works(formdef, include_id=True)
    assert f2.category_id == formdef.category_id

    formdef_xml_with_id = formdef.export_to_xml(include_id=True)

    # check there's no reference to a non-existing category
    Category.wipe()
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=False).category_id is None
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=True).category_id is None

    # check an import that is not using id fields will find the category by its
    # name
    cat = Category()
    cat.id = '2'
    cat.name = 'test category'
    cat.store()
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=False).category_id == '2'
    assert FormDef.import_from_xml_tree(formdef_xml_with_id, include_id=True).category_id is None


def test_file_field(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.FileField(
            id='1',
            document_type={
                'id': 'justificatif-de-domicile',
                'fargo': True,
                'mimetypes': [
                    'application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'image/*',
                ],
            },
        )
    ]
    assert_xml_import_export_works(formdef, include_id=True)
    assert_xml_import_export_works(formdef)
    assert_json_import_export_works(formdef, include_id=True)
    assert_json_import_export_works(formdef)


def test_invalid_field_type(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.StringField(id='1')]
    export = ET.tostring(export_to_indented_xml(formdef)).replace(b'<type>string</type>', b'<type>xxx</type>')
    with pytest.raises(FormdefImportError):
        FormDef.import_from_xml(io.BytesIO(export), include_id=True)


def test_invalid_data_source(pub):
    # manually edited exports
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.StringField(id='1', data_source={'type': 'xxx'})]
    export = ET.tostring(formdef.export_to_xml(include_id=False))
    export = export.replace(
        b'<data_source><type>xxx</type></data_source>', b'<data_source><type/></data_source>'
    )
    formdef2 = FormDef.import_from_xml(io.BytesIO(export))
    assert formdef2.fields[0].data_source == {}

    export = export.replace(b'<data_source><type/></data_source>', b'<data_source>    </data_source>')
    formdef2 = FormDef.import_from_xml(io.BytesIO(export))
    assert formdef2.fields[0].data_source == {}


def test_unknown_data_source(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.StringField(id='1', data_source={'type': 'json', 'value': 'http://example.net'})]
    export = ET.tostring(export_to_indented_xml(formdef))

    FormDef.import_from_xml(io.BytesIO(export))

    formdef.fields = [fields.StringField(id='1', data_source={'type': 'foobar'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    with pytest.raises(FormdefImportError) as excinfo:
        FormDef.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == 'Unknown datasources: foobar'

    # carddef as datasource
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.store()

    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:foo'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    FormDef.import_from_xml(io.BytesIO(export))

    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:unknown'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    with pytest.raises(FormdefImportError):
        FormDef.import_from_xml(io.BytesIO(export))

    # cards filtered on user
    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:foo:_with_user_filter'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    FormDef.import_from_xml(io.BytesIO(export))

    # carddef custom view as datasource
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:foo:card-view'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    FormDef.import_from_xml(io.BytesIO(export))

    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:foo:unknown'})]
    export = ET.tostring(export_to_indented_xml(formdef))
    with pytest.raises(FormdefImportError):
        FormDef.import_from_xml(io.BytesIO(export))


def test_formdef_with_block(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.BlockField(id='1', block_slug='foobar')]

    BlockDef.wipe()
    export = ET.tostring(export_to_indented_xml(formdef))
    with pytest.raises(FormdefImportError) as excinfo:
        FormDef.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == 'Unknown blocks of fields: foobar'


def test_formdef_with_block_legacy(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.BlockField(id='1', block_slug='foobar')]

    export = (
        ET.tostring(export_to_indented_xml(formdef))
        .replace(b'<block_slug type="str">foobar</block_slug', b'')
        .replace(b'<type>block</type>', b'<type>block:foobar</type>')
    )
    formdef = FormDef.import_from_xml(io.BytesIO(export))
    assert formdef.fields[0].key == 'block'
    assert formdef.fields[0].block_slug == 'foobar'


def test_duplicated_field_ids(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='2'),
        fields.StringField(id='2'),
        fields.StringField(id='1'),
    ]
    export = ET.tostring(export_to_indented_xml(formdef, include_id=True))

    with pytest.raises(FormdefImportError):
        FormDef.import_from_xml(io.BytesIO(export))

    with pytest.raises(FormdefImportError):
        FormDef.import_from_xml(io.BytesIO(export), include_id=True)

    formdef2 = FormDef.import_from_xml(io.BytesIO(export), fix_on_error=True)
    assert formdef2.fields[0].id == '1'
    assert formdef2.fields[1].id == '2'
    assert formdef2.fields[2].id == '3'


def test_page_condition(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.PageField(id='1', condition={'type': 'django', 'value': 'blah'})]
    fd2 = assert_xml_import_export_works(formdef)
    assert fd2.fields[0].key == 'page'
    assert fd2.fields[0].condition == formdef.fields[0].condition


def test_page_post_conditions(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.PageField(
            id='1',
            post_conditions=[{'condition': {'type': 'django', 'value': 'blah'}, 'error_message': 'bar'}],
        ),
    ]
    fd2 = assert_xml_import_export_works(formdef)
    assert fd2.fields[0].key == 'page'
    assert fd2.fields[0].post_conditions == formdef.fields[0].post_conditions

    # test incomplete post condition (not allowed anymore but old formdefs may
    # have this)
    formdef.fields = [
        fields.PageField(
            id='1',
            post_conditions=[{'condition': {'type': 'django', 'value': 'blah'}, 'error_message': None}],
        ),
    ]
    formdef_xml = formdef.export_to_xml(include_id=True)
    fd2 = FormDef.import_from_xml_tree(formdef_xml, include_id=True)
    assert fd2.fields[0].post_conditions[0]['condition'] == {'type': 'django', 'value': 'blah'}
    assert fd2.fields[0].post_conditions[0]['error_message'] == ''


def test_geolocations(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.geolocations = {'base': 'Base'}
    fd2 = assert_xml_import_export_works(formdef, include_id=True)
    assert fd2.geolocations == formdef.geolocations
    fd3 = assert_json_import_export_works(formdef)
    assert fd3.geolocations == formdef.geolocations


def test_workflow_roles(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='blah')
    role.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    fd2 = assert_xml_import_export_works(formdef, include_id=True)
    assert fd2.workflow_roles.get('_receiver') == role.id

    fd2 = assert_xml_import_export_works(formdef, include_id=False)
    assert fd2.workflow_roles.get('_receiver') == role.id

    xml_export = export_to_indented_xml(formdef, include_id=True)

    # same id, different name
    role.name = 'blah 2'
    role.store()

    fd2 = FormDef.import_from_xml_tree(xml_export, include_id=True)
    assert fd2.workflow_roles.get('_receiver') == role.id

    # found by slug
    fd2 = FormDef.import_from_xml_tree(xml_export, include_id=False)
    assert fd2.workflow_roles.get('_receiver') == role.id

    role.slug = 'something else'
    role.store()
    fd2 = FormDef.import_from_xml_tree(xml_export, include_id=False)
    assert fd2.workflow_roles.get('_receiver') is None

    role.remove_self()
    fd2 = FormDef.import_from_xml_tree(xml_export, include_id=True)
    assert fd2.workflow_roles.get('_receiver') is None


def test_user_roles(pub):
    pub.role_class.wipe()

    role = pub.role_class(name='blah')
    role.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.roles = ['logged-users', role.id]
    fd2 = assert_xml_import_export_works(formdef, include_id=True)
    assert fd2.roles == formdef.roles

    formdef_xml = formdef.export_to_xml(include_id=True)
    formdef_xml_no_id = formdef.export_to_xml(include_id=False)
    role.remove_self()
    fd2 = FormDef.import_from_xml_tree(formdef_xml, include_id=True)
    assert fd2.roles == ['logged-users']
    fd2 = FormDef.import_from_xml_tree(formdef_xml, include_id=False)
    assert fd2.roles == ['logged-users']

    fd2 = FormDef.import_from_xml_tree(formdef_xml_no_id, include_id=True)
    assert fd2.roles == ['logged-users']
    fd2 = FormDef.import_from_xml_tree(formdef_xml_no_id, include_id=False)
    assert fd2.roles == ['logged-users']


def test_backoffice_submission_roles(pub):
    pub.role_class.wipe()

    role = pub.role_class(name='blah')
    role.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.backoffice_submission_roles = [role.id]
    fd2 = assert_xml_import_export_works(formdef, include_id=True)
    assert fd2.backoffice_submission_roles == formdef.backoffice_submission_roles


def test_required_authentication_contexts(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.required_authentication_contexts = ['fedict']
    fd2 = assert_xml_import_export_works(formdef, include_id=True)
    assert fd2.required_authentication_contexts == formdef.required_authentication_contexts


def test_field_condition(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id=1, label='Bar', size='40', condition={'type': 'django', 'value': '1'})
    ]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].condition == {'type': 'django', 'value': '1'}


def test_field_validation(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [fields.StringField(id=1, label='Bar', size='40', validation={})]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert not f2.fields[0].validation

    formdef.fields = [fields.StringField(id=1, label='Bar', size='40', validation=None)]
    formdef_xml = formdef.export_to_xml()
    f2 = FormDef.import_from_xml_tree(formdef_xml)
    assert len(f2.fields) == len(formdef.fields)
    assert not f2.fields[0].validation

    formdef.fields = [
        fields.StringField(id=1, label='Bar', size='40', validation={'type': 'regex', 'value': r'\d'})
    ]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].validation == formdef.fields[0].validation

    # backward compatibility
    formdef_xml = formdef.export_to_xml()
    old_format = ET.tostring(formdef_xml).replace(
        b'<validation><type>regex</type><value>\\d</value></validation>', b'<validation>\\d</validation>'
    )
    f2 = FormDef.import_from_xml(io.BytesIO(old_format))
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].validation == {'type': 'regex', 'value': '\\d'}


def test_digest_templates(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = []
    formdef.digest_templates = {'default': '{{form_number}}', 'custom-view:foo-bar': 'plop'}
    f2 = assert_xml_import_export_works(formdef)
    assert f2.digest_templates == formdef.digest_templates


def test_field_prefill(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id=1, label='Bar', size='40', prefill={'type': 'string', 'value': 'plop'})
    ]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].prefill == {'type': 'string', 'value': 'plop'}

    formdef.fields = [
        fields.StringField(
            id=1,
            label='Bar',
            size='40',
            prefill={'type': 'string', 'value': 'plop', 'locked': True, 'locked-unless-empty': True},
        )
    ]
    f2 = assert_xml_import_export_works(formdef)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].prefill == {
        'type': 'string',
        'value': 'plop',
        'locked': True,
        'locked-unless-empty': True,
    }

    formdef.fields = [
        fields.StringField(
            id=1,
            label='Bar',
            size='40',
            prefill={'type': 'string', 'value': 'plop', 'locked': False, 'locked-unless-empty': False},
        )
    ]
    formdef_xml = formdef.export_to_xml()
    f2 = FormDef.import_from_xml_tree(formdef_xml)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].prefill == {'type': 'string', 'value': 'plop'}

    formdef.fields = [
        fields.StringField(
            id=1,
            label='Bar',
            size='40',
            prefill={'type': 'string', 'value': 'plop', 'locked': False},
        )
    ]
    formdef_xml_str = ET.tostring(formdef.export_to_xml())
    formdef_xml_str = formdef_xml_str.replace(b'<value>plop</value>', b'')
    formdef_xml = ET.fromstring(formdef_xml_str)
    f2 = FormDef.import_from_xml_tree(formdef_xml)
    assert len(f2.fields) == len(formdef.fields)
    assert f2.fields[0].prefill == {'type': 'string', 'value': None}


def test_custom_views(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
        fields.StringField(id='2', label='Bar', varname='bar'),
    ]
    formdef.store()

    role = pub.role_class(name='Test')
    role.store()

    # define also custom views
    pub.custom_view_class.wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared form view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view.filters = {'filter': 'done', 'filter-1': 'on', 'filter-status': 'on', 'filter-1-value': 'b'}
    custom_view.visibility = 'any'
    custom_view.is_default = True
    custom_view.order_by = 'receipt_time'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private form view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.user_id = '42'
    custom_view.order_by = 'id'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared form view on role'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.order_by = 'id'
    custom_view.visibility = 'role'
    custom_view.role_id = role.id
    custom_view.store()

    formdef_xml = formdef.export_to_xml()
    assert formdef_xml.tag == 'formdef'
    assert b'private' not in ET.tostring(formdef_xml)
    formdef.data_class().wipe()
    pub.custom_view_class.wipe()

    formdef2 = FormDef.import_from_xml(io.BytesIO(ET.tostring(formdef_xml)))
    assert formdef2.name == 'foo'
    assert formdef2._custom_views

    custom_views = formdef2._custom_views
    custom_views.sort(key=lambda x: x.slug)
    assert len(custom_views) == 2
    assert custom_views[0].title == 'shared form view'
    assert custom_views[0].slug == 'shared-form-view'
    assert custom_views[0].is_default is True
    assert custom_views[0].columns == {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    assert custom_views[0].filters == {
        'filter': 'done',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'b',
    }
    assert custom_views[0].visibility == 'any'
    assert custom_views[0].order_by == 'receipt_time'
    assert custom_views[0].formdef_id is None
    assert custom_views[0].formdef_type is None

    assert custom_views[1].title == 'shared form view on role'
    assert custom_views[1].slug == 'shared-form-view-on-role'
    assert custom_views[1].columns == {'list': [{'id': 'id'}]}
    assert custom_views[1].filters == {}
    assert custom_views[1].visibility == 'role'
    assert custom_views[1].role_id == role.id
    assert custom_views[1].order_by == 'id'
    assert custom_views[1].formdef_id is None
    assert custom_views[1].formdef_type is None

    formdef2.store()
    custom_views = pub.custom_view_class.select(order_by='slug')
    assert len(custom_views) == 2
    assert custom_views[0].title == 'shared form view'
    assert custom_views[0].slug == 'shared-form-view'
    assert custom_views[0].columns == {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    assert custom_views[0].filters == {
        'filter': 'done',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'b',
    }
    assert custom_views[0].visibility == 'any'
    assert custom_views[0].order_by == 'receipt_time'
    assert custom_views[0].formdef_id == str(formdef2.id)
    assert custom_views[0].formdef_type == 'formdef'

    assert custom_views[1].title == 'shared form view on role'
    assert custom_views[1].slug == 'shared-form-view-on-role'
    assert custom_views[1].columns == {'list': [{'id': 'id'}]}
    assert custom_views[1].filters == {}
    assert custom_views[1].visibility == 'role'
    assert custom_views[1].role_id == role.id
    assert custom_views[1].order_by == 'id'
    assert custom_views[1].formdef_id == str(formdef2.id)
    assert custom_views[1].formdef_type == 'formdef'


def test_custom_views_include_id(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
        fields.StringField(id='2', label='Bar', varname='bar'),
    ]
    formdef.store()

    role1 = pub.role_class(name='Test1')
    role1.store()
    role2 = pub.role_class(name='Test2')
    role2.store()

    pub.custom_view_class.wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared form view on role'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.order_by = 'id'
    custom_view.visibility = 'role'
    custom_view.role_id = role1.id
    custom_view.store()

    formdef_xml = formdef.export_to_xml(include_id=True)
    formdef.data_class().wipe()
    pub.custom_view_class.wipe()

    # recreate role, it will get a different id
    role1.remove_self()
    role3 = pub.role_class(name='Test1')
    role3.store()
    role2.remove_self()

    formdef2 = FormDef.import_from_xml(io.BytesIO(ET.tostring(formdef_xml)), include_id=False)
    formdef2.store()

    custom_views = formdef2._custom_views
    assert custom_views[0].title == 'shared form view on role'
    assert custom_views[0].slug == 'shared-form-view-on-role'
    assert custom_views[0].columns == {'list': [{'id': 'id'}]}
    assert custom_views[0].filters == {}
    assert custom_views[0].visibility == 'role'
    assert custom_views[0].role_id == role3.id  # will match on slug
    assert custom_views[0].order_by == 'id'
    assert custom_views[0].formdef_id == str(formdef2.id)
    assert custom_views[0].formdef_type == 'formdef'


def test_import_formdef_multiple_errors(pub):
    BlockDef.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.BlockField(id='1', block_slug='foobar'),
        fields.BlockField(id='2', block_slug='foobaz'),
        fields.StringField(id='3', data_source={'type': 'foobar'}),
        fields.StringField(id='4', data_source={'type': 'carddef:unknown'}),
        fields.BoolField(id='5'),
    ]

    export = ET.tostring(export_to_indented_xml(formdef)).replace(
        b'<type>bool</type>', b'<type>foobaz</type>'
    )
    with pytest.raises(FormdefImportError) as excinfo:
        FormDef.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == (
        'Unknown blocks of fields: foobar, foobaz; '
        'Unknown datasources: carddef:unknown, foobar; '
        'Unknown field types: foobaz'
    )


def test_import_formdef_root_node_error():
    export = b'<wrong_root_node><name>Name</name></wrong_root_node>'
    with pytest.raises(FormdefImportError) as excinfo:
        FormDef.import_from_xml(io.BytesIO(export))
    assert (
        excinfo.value.msg
        == 'Provided XML file is invalid, it starts with a <wrong_root_node> tag instead of <formdef>'
    )

    with pytest.raises(FormdefImportError) as excinfo:
        CardDef.import_from_xml(io.BytesIO(export))
    assert (
        excinfo.value.msg
        == 'Provided XML file is invalid, it starts with a <wrong_root_node> tag instead of <carddef>'
    )


def test_tracking_code_attributes(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.confirmation = True
    formdef.enable_tracking_codes = True
    for verify_fields in (['1', '2'], [], None):
        formdef.tracking_code_verify_fields = verify_fields
        f2 = assert_xml_import_export_works(formdef)
        assert f2.enable_tracking_codes == formdef.enable_tracking_codes
        assert f2.tracking_code_verify_fields == formdef.tracking_code_verify_fields
        assert f2.confirmation == formdef.confirmation
        f2 = assert_json_import_export_works(formdef)
        assert f2.enable_tracking_codes == formdef.enable_tracking_codes
        assert f2.tracking_code_verify_fields == formdef.tracking_code_verify_fields
        assert f2.confirmation == formdef.confirmation


def test_management_sidebar_items(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.management_sidebar_items = {'general', 'pending-forms'}
    f2 = assert_xml_import_export_works(formdef)
    assert f2.management_sidebar_items == {'general', 'pending-forms'}


def test_submission_sidebar_items(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.submission_sidebar_items = {'general', 'submission-context'}
    f2 = assert_xml_import_export_works(formdef)
    assert f2.submission_sidebar_items == {'general', 'submission-context'}


def test_workflow_migrations_attribute(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.workflow_migrations = {
        '_default other': {
            'old_workflow': '_default',
            'new_workflow': 'other',
            'timestamp': '2024-06-19T11:18:45.402521+02:00',
            'status_mapping': {
                '1': '2',
                '2': '3',
            },
        }
    }
    f2 = assert_xml_import_export_works(formdef)
    assert f2.workflow_migrations == formdef.workflow_migrations


def test_computed_field_with_True_as_value_template(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.fields = [fields.ComputedField(id=1, value_template='True', size='40')]
    f2 = assert_xml_import_export_works(formdef)
    assert f2.fields[0].value_template == 'True'

    # check invalid files are imported correctly
    export = ET.tostring(export_to_indented_xml(formdef))
    export = export.replace(b'<value_template type="str>', b'<value_template type="bool">')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].value_template == 'True'


def test_field_required_boolean_to_string(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.fields = [fields.StringField(id=1, required='required')]
    formdef2 = assert_xml_import_export_works(formdef)
    assert formdef2.fields[0].required == 'required'

    # check old files are imported correctly
    export = ET.tostring(export_to_indented_xml(formdef))
    export = export.replace(b'<required type="str">required', b'<required type="bool">True')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].required == 'required'

    export = ET.tostring(export_to_indented_xml(formdef))
    export = export.replace(b'<required type="str">required', b'<required type="bool">False')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].required == 'optional'


def test_map_field_with_default_position(pub):
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.fields = [fields.MapField(id=1, label='position', default_position={'lat': 13, 'lon': 12})]
    export = ET.tostring(export_to_indented_xml(formdef))
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].default_position == {'lat': 13, 'lon': 12}

    # backward compatibility
    formdef.fields = [fields.MapField(id=1, label='position', default_position='13;12')]
    export = ET.tostring(export_to_indented_xml(formdef))
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].default_position == {'lat': 13, 'lon': 12}


def test_block_field_int_to_string(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = []
    block.store()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.url_name = 'foo'
    formdef.fields = [fields.BlockField(id=1, block_slug='foobar', default_items_count='2', max_items='2')]
    formdef2 = assert_xml_import_export_works(formdef)
    assert formdef2.fields[0].default_items_count == '2'
    assert formdef2.fields[0].max_items == '2'

    # check past files are imported correctly
    export = ET.tostring(export_to_indented_xml(formdef))
    export = export.replace(b'<default_items_count type="str">2', b'<default_items_count type="int">2')
    export = export.replace(b'<max_items type="str">2', b'<max_items type="int">2')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].default_items_count == '2'
    assert formdef2.fields[0].max_items == '2'

    # check files with missing values are imported correctly
    export = ET.tostring(export_to_indented_xml(formdef))
    export = export.replace(b'<default_items_count type="str">2', b'<default_items_count type="str">')
    export = export.replace(b'<max_items type="str">2', b'<max_items type="str">')
    formdef2 = FormDef.import_from_xml_tree(ET.fromstring(export))
    assert formdef2.fields[0].default_items_count == '1'
    assert formdef2.fields[0].max_items == '1'
