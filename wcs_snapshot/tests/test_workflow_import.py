import decimal
import io
import re
import xml.etree.ElementTree as ET

import pytest
from quixote.http_request import Upload

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import WorkflowCategory
from wcs.fields import BlockField, BoolField, EmailField, FileField, ItemField, NumericField, StringField
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.form import UploadedFile
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import (
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowImportError,
    WorkflowVariablesFieldsFormDef,
)

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def export_to_indented_xml(workflow, include_id=False):
    workflow_xml = workflow.export_to_xml(include_id=include_id)
    ET.indent(workflow_xml)
    return workflow_xml


def assert_import_export_works(wf, include_id=False):
    wf2 = Workflow.import_from_xml_tree(ET.fromstring(ET.tostring(wf.export_to_xml(include_id))), include_id)
    assert ET.tostring(export_to_indented_xml(wf)) == ET.tostring(export_to_indented_xml(wf2))
    return wf2


def test_empty(pub):
    wf = Workflow(name='empty')
    assert_import_export_works(wf)


def test_status(pub):
    wf = Workflow(name='status')
    wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')
    assert_import_export_works(wf)


def test_status_actions(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']

    assert_import_export_works(wf)


def test_status_actions_forced_include_id(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']

    wf2 = Workflow.import_from_xml_tree(wf.export_to_xml(include_id=True), include_id=False)
    assert wf2.get_status('st1').items[0].id == '_commentable'


def test_status_colour_css_class(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    st1.extra_css_class = 'hello'
    st1.colour = '#FF0000'
    wf.add_status('Status2', 'st2')
    assert_import_export_works(wf)


def test_status_forced_endpoint(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    st1.forced_endpoint = True
    wf.add_status('Status2', 'st2')
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].forced_endpoint is True
    assert wf2.possible_status[1].forced_endpoint is False


def test_status_with_loop(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].loop_items_template == '{{ "abc"|make_list }}'
    assert wf2.possible_status[0].after_loop_status == wf2.possible_status[1].id


def test_default_wf(pub):
    wf = Workflow.get_default_workflow()
    assert_import_export_works(wf)


def test_action_dispatch(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    role = pub.role_class()
    role.id = '5'
    role.name = 'Test Role'
    role.store()

    dispatch = st1.add_action('dispatch', id='_x')
    dispatch.role_id = 5
    dispatch.role_key = 'plop'

    wf2 = assert_import_export_works(wf)

    # checks role id is imported as integer
    assert wf2.possible_status[0].items[0].role_id == '5'

    pub.cfg['sp'] = {'idp-manage-roles': True}
    # now roles are managed: cannot create them
    xml_export_orig = (
        ET.tostring(export_to_indented_xml(wf, include_id=True))
        .replace(b'slug="test-role"', b'slug="unknown-role"')
        .replace(b'role_id="5"', b'role_id="23"')
        .replace(b'>Test Role<', b'>unknown<')
    )
    with pytest.raises(WorkflowImportError) as excinfo:
        wf2 = Workflow.import_from_xml_tree(ET.fromstring(xml_export_orig), include_id=True)
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == 'Unknown roles: unknown'
    # allow computed roles
    dispatch.role_id = '{{ form_var_bar }}'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].role_id == '{{ form_var_bar }}'
    dispatch.role_id = 'Role {{ form_var_foo }}'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].role_id == 'Role {{ form_var_foo }}'
    dispatch.role_id = 'Role [form_var_foo]'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].role_id == 'Role [form_var_foo]'
    # and even straight user email address
    dispatch.role_id = 'foo@example.invalid'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].role_id == 'foo@example.invalid'

    dispatch.role_id = 'Rolé [form_var_foo]'
    wf2 = assert_import_export_works(wf, include_id=False)
    assert wf2.possible_status[0].items[0].role_id == 'Rolé [form_var_foo]'

    dispatch.role_id = 'Rolé [form_var_foo]'
    wf2 = assert_import_export_works(wf, include_id=True)
    assert wf2.possible_status[0].items[0].role_id == 'Rolé [form_var_foo]'


def test_status_actions_named_role(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['logged-users']

    assert_import_export_works(wf)


def test_status_actions_named_existing_role(pub):
    role = pub.role_class()
    role.id = '2'
    role.name = 'Test Role named existing role'
    role.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [2]

    wf2 = assert_import_export_works(wf)
    assert re.findall(
        '<item.*role_id="2".*>Test Role named existing role</item>',
        ET.tostring(wf.export_to_xml()).decode(),
    )
    assert wf2.possible_status[0].items[0].by == ['2']

    # check that it works even if the role_id is not set
    xml_export_orig = ET.tostring(export_to_indented_xml(wf))
    xml_export = xml_export_orig.replace(b'role_id="2"', b'')
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf3.possible_status[0].items[0].by == ['2']

    # check that it works even if role_id and slug are not set
    xml_export_orig = ET.tostring(export_to_indented_xml(wf))
    xml_export = xml_export_orig.replace(b'role_id="2"', b'')
    xml_export = xml_export_orig.replace(b'slug="test-role-named-existing-role"', b'')
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf3.possible_status[0].items[0].by == ['2']


def test_status_actions_named_missing_role(pub):
    role = pub.role_class()
    role.id = '3'
    role.name = 'Test Role A'
    role.store()

    role = pub.role_class()
    role.id = '4'
    role.name = 'Test Role B'
    role.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [3]

    assert_import_export_works(wf)

    # check that role name has precedence over id
    xml_export_orig = ET.tostring(export_to_indented_xml(wf))
    assert b'role_id="3"' in xml_export_orig
    xml_export = xml_export_orig.replace(b'role_id="3"', b'role_id="4"').replace(b'slug="test-role-a"', b'')
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf3.possible_status[0].items[0].by == ['3']

    # check that it creates a new role if there's no match on id and name
    xml_export = (
        xml_export_orig.replace(b'role_id="3"', b'role_id="999"')
        .replace(b'slug="test-role-a"', b'')
        .replace(b'Test Role A', b'foobar')
    )
    nb_roles = pub.role_class.count()
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert pub.role_class.count() == nb_roles + 1

    # check that it doesn't fallback on the id if there's no match on the
    # name
    nb_roles = pub.role_class.count()
    xml_export = xml_export_orig.replace(b'Test Role A', b'Test Role C').replace(b'slug="test-role-a"', b'')
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf3.possible_status[0].items[0].by != ['3']
    assert pub.role_class.count() == nb_roles + 1

    # on the other hand, check that it uses the id when included_id is True
    wf3 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)), include_id=True)
    assert wf3.possible_status[0].items[0].by == ['3']


def test_display_form_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(StringField(label='Test'))
    display_form.formdef.fields.append(StringField(label='Test2'))
    display_form.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'a == b'},
            'error_message': 'You shall not pass.',
        }
    ]

    # check action id is not lost when using include_id
    wf2 = assert_import_export_works(wf, include_id=True)
    assert wf2.possible_status[0].items[0].id == display_form.id


def test_export_to_model_action(pub):
    wf = Workflow(name='status')
    wf.store()
    st1 = wf.add_status('Status1', 'st1')

    export_to = st1.add_action('export_to_model')
    export_to.label = 'test'
    upload = Upload('/foo/bar', content_type='application/vnd.oasis.opendocument.text')
    file_content = b'''PK\x03\x04\x14\x00\x00\x08\x00\x00\'l\x8eG^\xc62\x0c\'\x00'''
    upload.fp = io.BytesIO()
    upload.fp.write(file_content)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.APP_DIR, None, upload)

    assert wf.possible_status[0].items[0].model_file.base_filename == 'bar'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].model_file.base_filename == 'bar'
    assert wf2.possible_status[0].items[0].model_file.get_file().read() == file_content

    # and test with an empty file
    st1.items = []
    export_to = st1.add_action('export_to_model')
    export_to.label = 'test'
    upload = Upload('/foo/bar', content_type='text/rtf')
    file_content = b''
    upload.fp = io.BytesIO()
    upload.fp.write(file_content)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.APP_DIR, None, upload)
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].model_file.get_file().read() == file_content

    wf2 = assert_import_export_works(wf, include_id=True)
    wf3 = assert_import_export_works(wf2, include_id=True)
    assert (
        wf2.possible_status[0].items[0].model_file.filename
        == wf3.possible_status[0].items[0].model_file.filename
    )

    wf3 = assert_import_export_works(wf2, include_id=False)
    assert (
        wf2.possible_status[0].items[0].model_file.filename
        != wf3.possible_status[0].items[0].model_file.filename
    )


def test_export_roles(pub):
    wf = Workflow(name='roles')
    wf.roles = {'foo': 'Bar'}
    wf2 = assert_import_export_works(wf)
    assert wf2.roles == wf.roles


def test_jump_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.condition = {'type': 'django', 'value': '1'}
    jump.trigger = 'bar'
    jump.timeout = 1200
    jump.status = 'st2'

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].condition == {'type': 'django', 'value': '1'}
    assert wf2.possible_status[0].items[0].trigger == 'bar'
    assert wf2.possible_status[0].items[0].timeout == 1200


def test_commentable_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.button_label = None

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].button_label is None
    assert wf2.possible_status[0].items[0].required is False

    commentable.required = True
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].required is True

    # import legacy comment without required attribute
    xml_export = ET.tostring(export_to_indented_xml(wf))
    assert b'<required>True</required>' in xml_export
    xml_export = xml_export.replace(b'<required>True</required>', b'')
    assert b'<required>True</required>' not in xml_export
    wf2 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf2.possible_status[0].items[0].required is False

    commentable.button_label = 'button label'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].button_label == 'button label'


def test_variables_formdef(pub):
    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(StringField(label='Test'))
    wf2 = assert_import_export_works(wf)
    assert wf2.variables_formdef.fields[0].label == 'Test'


def test_variables_formdef_default_value(pub):
    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(StringField(label='Test', default_value='123'))
    wf2 = assert_import_export_works(wf)
    assert wf2.variables_formdef.fields[0].default_value == '123'


def test_variables_formdef_numeric_default_value(pub):
    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(
        NumericField(
            label='Test',
            min_value=decimal.Decimal('0'),
            max_value=decimal.Decimal('1000'),
            default_value=decimal.Decimal('100'),
        )
    )
    wf2 = assert_import_export_works(wf)
    assert wf2.variables_formdef.fields[0].default_value == decimal.Decimal('100')
    xml_export = ET.tostring(export_to_indented_xml(wf))
    assert b'>100<' in xml_export
    assert b'>1000<' in xml_export
    xml_export = xml_export.replace(b'>100<', b'>1E+2<')
    wf2 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf2.variables_formdef.fields[0].default_value == decimal.Decimal('100')


def test_wscall_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    wscall = st1.add_action('webservice_call', id='_wscall')
    wscall.url = 'http://test/'
    wscall.varname = 'varname'
    wscall.post = False
    wscall.request_signature_key = 'key'
    wscall.post_data = {'one': '1', 'two': '=2', 'good:name': 'ok', 'empty': ''}
    wscall.qs_data = {'one': '2', 'two': '=3', 'good:name': 'ok', 'empty': ''}

    wf2 = assert_import_export_works(wf)
    wscall2 = wf2.possible_status[0].items[0]
    assert wscall2.url == 'http://test/'
    assert wscall2.varname == 'varname'
    assert wscall2.post is False
    assert wscall2.request_signature_key == 'key'
    assert wscall2.post_data == {'one': '1', 'two': '=2', 'good:name': 'ok', 'empty': ''}
    assert wscall2.qs_data == {'one': '2', 'two': '=3', 'good:name': 'ok', 'empty': ''}


def test_backoffice_info_text(pub):
    wf = Workflow(name='info texts')
    st1 = wf.add_status('Status1', 'st1')
    st1.backoffice_info_text = '<p>Foo</p>'

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.backoffice_info_text = '<p>Bar</p>'

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].backoffice_info_text == '<p>Foo</p>'
    assert wf2.possible_status[0].items[0].backoffice_info_text == '<p>Bar</p>'


def test_global_actions(pub):
    role = pub.role_class()
    role.id = '5'
    role.name = 'Test Role'
    role.store()

    wf = Workflow(name='global actions')
    wf.add_status('Status1', 'st1')
    ac1 = wf.add_global_action('Action', 'ac1')
    ac1.backoffice_info_text = '<p>Foo</p>'

    add_to_journal = ac1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'

    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = [role.id]
    trigger.statuses = ['st1']

    wf2 = assert_import_export_works(wf)
    assert wf2.global_actions[0].triggers[0].roles == [role.id]

    trigger.allow_as_mass_action = False
    wf2 = assert_import_export_works(wf)
    assert wf2.global_actions[0].triggers[0].allow_as_mass_action is False

    wf2 = assert_import_export_works(wf, True)

    display_form = ac1.add_action('form', id='_form')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [StringField(label='Test', data_source={'type': 'foobar'})]
    wf.store()

    export = ET.tostring(export_to_indented_xml(wf))
    with pytest.raises(WorkflowImportError) as excinfo:
        Workflow.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == 'Unknown datasources: foobar'


def test_register_comment_to(pub):
    role = pub.role_class()
    role.id = '5'
    role.name = 'Test Role'
    role.store()

    wf = Workflow(name='global actions')
    st1 = wf.add_status('Status1', 'st1')

    add_to_journal1 = st1.add_action('register-comment', id='_add_to_journal1')
    add_to_journal1.comment = 'HELLO WORLD'

    add_to_journal2 = st1.add_action('register-comment', id='_add_to_journal2')
    add_to_journal2.comment = 'OLA MUNDO'
    add_to_journal2.to = [role.id]
    assert wf.possible_status[0].items[0].to is None
    assert wf.possible_status[0].items[1].to == [role.id]

    xml_root = wf.export_to_xml()
    assert 'to' not in [x.tag for x in xml_root.findall('possible_status/status/items/item[1]/')]
    assert 'to' in [x.tag for x in xml_root.findall('possible_status/status/items/item[2]/')]

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].to == []
    assert wf2.possible_status[0].items[1].to == [role.id]


def test_backoffice_fields(pub):
    wf = Workflow(name='bo fields')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(
            id='bo1', label='1st backoffice field', varname='backoffice_blah', display_locations=None
        ),
    ]
    wf2 = assert_import_export_works(wf)
    assert wf2.backoffice_fields_formdef.fields[0].display_locations == []


def test_complex_dispatch_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    pub.role_class.wipe()

    role1 = pub.role_class()
    role1.name = 'Test Role 1'
    role1.store()

    role2 = pub.role_class()
    role2.name = 'Test Role 2'
    role2.store()

    dispatch = st1.add_action('dispatch', id='_dispatch')
    dispatch.role_key = '_receiver'
    dispatch.dispatch_type = 'automatic'
    dispatch.variable = 'plop'
    dispatch.rules = [{'value': 'a', 'role_id': role1.id}, {'value': 'b', 'role_id': role2.id}]

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].variable == dispatch.variable
    assert wf2.possible_status[0].items[0].rules == dispatch.rules
    assert wf2.possible_status[0].items[0].dispatch_type == 'automatic'

    pub.role_class.wipe()

    role3 = pub.role_class()
    role3.name = 'Test Role 1'
    role3.store()

    role4 = pub.role_class()
    role4.name = 'Test Role 2'
    role4.store()

    xml_export_orig = export_to_indented_xml(wf, include_id=True)
    wf2 = Workflow.import_from_xml_tree(xml_export_orig)
    assert wf2.possible_status[0].items[0].variable == dispatch.variable
    assert wf2.possible_status[0].items[0].rules == [
        {'value': 'a', 'role_id': role3.id},
        {'value': 'b', 'role_id': role4.id},
    ]
    assert wf2.possible_status[0].items[0].dispatch_type == 'automatic'

    # check rules are not exported with dispatch type is not automatic
    dispatch.dispatch_type = 'manual'
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].dispatch_type == 'manual'
    assert not wf2.possible_status[0].items[0].rules
    xml_export = export_to_indented_xml(wf, include_id=True)
    assert xml_export.find('possible_status/status/items/item/rules') is None


def test_display_message_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display = st1.add_action('displaymsg')
    display.message = 'hey'
    display.to = ['_submitter', '1']

    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].message == display.message
    for role_id in display.to:
        assert role_id in wf2.possible_status[0].items[0].to

    wf2 = assert_import_export_works(wf, include_id=True)


def test_sendmail_other_destination(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    sendmail = st1.add_action('sendmail')
    sendmail.to = ['_submitter']

    pub.role_class.wipe()
    wf2 = assert_import_export_works(wf)
    assert pub.role_class.count() == 0

    sendmail.to = [
        '[form_var_plop]',
        '{{ form_var_plop }}',
        'foobar@localhost',
    ]
    wf2 = assert_import_export_works(wf)
    assert pub.role_class.count() == 0
    assert wf2.possible_status[0].items[0].to == sendmail.to


def test_sendmail_attachments(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    sendmail = st1.add_action('sendmail')

    sendmail.attachments = ['{{ form_var_file_raw }}', 'form_fbo1']
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].attachments == sendmail.attachments

    sendmail.attachments = []
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].attachments == []


def test_sms(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    sendsms = st1.add_action('sendsms')
    sendsms.to = ['0123456789', '']
    sendsms.body = 'hello'

    pub.role_class.wipe()
    wf2 = assert_import_export_works(wf)
    assert pub.role_class.count() == 0
    assert wf2.possible_status[0].items[0].to == sendsms.to


def test_criticality_level(pub):
    wf = Workflow(name='criticality level')
    wf.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red', colour='#FF0000'),
    ]

    wf2 = assert_import_export_works(wf)
    assert wf2.criticality_levels[0].name == 'green'
    assert wf2.criticality_levels[1].name == 'yellow'


def test_global_timeout_trigger(pub):
    wf = Workflow(name='global actions')
    ac1 = wf.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('timeout')
    trigger.timeout = '2'
    trigger.anchor = 'creation'

    wf2 = Workflow.import_from_xml_tree(ET.fromstring(ET.tostring(wf.export_to_xml(True))), True)
    assert wf2.global_actions[0].triggers[-1].id == trigger.id
    assert wf2.global_actions[0].triggers[-1].anchor == trigger.anchor


def test_global_anchor_expression_trigger(pub):
    wf = Workflow(name='global actions')
    ac1 = wf.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('timeout')
    trigger.anchor_expression = 'False'
    trigger.anchor_template = '{{x}}'
    trigger.anchor = 'template'

    wf2 = assert_import_export_works(wf, include_id=True)
    assert wf2.global_actions[0].triggers[-1].id == trigger.id
    assert wf2.global_actions[0].triggers[-1].anchor == trigger.anchor
    assert wf2.global_actions[0].triggers[-1].anchor_expression == trigger.anchor_expression
    assert wf2.global_actions[0].triggers[-1].anchor_template == trigger.anchor_template


def test_global_webservice_trigger(pub):
    wf = Workflow(name='global actions')
    ac1 = wf.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('webservice')
    trigger.identifier = 'plop'

    wf2 = assert_import_export_works(wf, include_id=True)
    assert wf2.global_actions[0].triggers[-1].id == trigger.id
    assert wf2.global_actions[0].triggers[-1].identifier == trigger.identifier


def test_profile_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    item = st1.add_action('update_user_profile', id='_item')
    item.fields = [{'field_id': '__email', 'value': '=form_var_foo'}]

    wf2 = assert_import_export_works(wf)
    item2 = wf2.possible_status[0].items[0]
    assert item2.fields == [{'field_id': '__email', 'value': '=form_var_foo'}]


@pytest.mark.parametrize('action_key', ['add_role', 'remove_role'])
def test_role_actions(pub, action_key):
    pub.role_class.wipe()

    role1 = pub.role_class()
    role1.name = 'Test Role'
    role1.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    item = st1.add_action(action_key)
    item.role_id = role1.id
    xml_export_orig = ET.tostring(export_to_indented_xml(wf))

    role = pub.role_class()
    role.name = 'Another role'
    role.store()

    role1.remove_self()

    role = pub.role_class()
    role.name = 'Test Role'
    role.store()

    wf2 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export_orig)))
    item2 = wf2.possible_status[0].items[0]
    assert item2.role_id == role.id  # found using slug match


def test_attachment_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    item = st1.add_action('addattachment', id='_foo')
    item.document_type = {
        'id': '_audio',
        'label': 'Sound files',
        'mimetypes': ['audio/*'],
    }

    wf2 = assert_import_export_works(wf)
    item2 = wf2.possible_status[0].items[0]
    assert item2.document_type == {
        'id': '_audio',
        'label': 'Sound files',
        'mimetypes': ['audio/*'],
    }


def test_set_backoffice_fields_action(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    item = st1.add_action('set-backoffice-fields', id='_item')
    item.fields = [{'field_id': 'bo1', 'value': '=form_var_foo'}]

    wf2 = assert_import_export_works(wf)
    item2 = wf2.possible_status[0].items[0]
    assert item2.fields == [{'field_id': 'bo1', 'value': '=form_var_foo'}]


def test_set_backoffice_fields_action_boolean(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    item = st1.add_action('set-backoffice-fields', id='_item')
    item.fields = [{'field_id': 'bo1', 'value': 'True'}]

    wf2 = assert_import_export_works(wf)
    item2 = wf2.possible_status[0].items[0]
    assert item2.fields == [{'field_id': 'bo1', 'value': 'True'}]


def test_action_condition(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    sendmail = st1.add_action('sendmail')

    wf2 = assert_import_export_works(wf)

    sendmail.condition = {'type': 'django', 'value': 'True'}
    wf2 = assert_import_export_works(wf)
    assert wf2.possible_status[0].items[0].condition == {'type': 'django', 'value': 'True'}


def test_create_formdata(pub):
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
        FileField(id='1', label='file', varname='foo_file'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')

    st1 = wf.add_status('New')
    st2 = wf.add_status('Resubmit')

    jump = st1.add_action('choice', id='_resubmit')
    jump.label = 'Resubmit'
    jump.by = ['_submitter']
    jump.status = st2.id

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='=form_var_toto_string'),
        Mapping(field_id='1', expression='=form_var_toto_file_raw'),
    ]

    redirect = st2.add_action('redirect_to_url', id='_redirect')
    redirect.url = '{{ form_links_resubmitted.form_url }}'

    jump = st2.add_action('jumponsubmit', id='_jump')
    jump.status = st1.id

    wf.store()

    assert_import_export_works(wf, include_id=True)


def test_external_workflow(pub):
    target_wf = Workflow(name='External global action')
    action = target_wf.add_global_action('Delete', 'delete')
    trigger = action.append_trigger('webservice')
    trigger.trigger_id = 'Cleanup'
    target_wf.store()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.workflow = target_wf
    target_formdef.store()

    wf = Workflow(name='External workflow call')
    st1 = wf.add_status('New')
    st2 = wf.add_status('Call external workflow')

    jump = st1.add_action('choice', id='_external')
    jump.label = 'Cleanup'
    jump.by = ['_submitter']
    jump.status = st2.id

    external_workflow = st2.add_action('external_workflow_global_action', id='_external_workflow')
    external_workflow.slug = 'formdef:%s' % target_formdef.url_name
    external_workflow.event = trigger.id

    wf.store()
    assert_import_export_works(wf, include_id=True)


def test_worklow_with_mail_template(pub):
    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()

    wf = Workflow(name='test mail template')
    st1 = wf.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = ['_receiver']
    item.mail_template = mail_template.slug
    wf.store()
    assert_import_export_works(wf, include_id=True)

    # import with non existing mail template
    MailTemplate.wipe()
    export = ET.tostring(wf.export_to_xml(include_id=True))
    with pytest.raises(WorkflowImportError) as excinfo:
        Workflow.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == 'Unknown mail templates: test-mail-template'

    item.mail_template = ''
    wf.store()
    export = ET.tostring(wf.export_to_xml(include_id=True))
    wf2 = Workflow.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert wf2.possible_status[0].items[0].mail_template is None


def test_workflow_with_unknown_data_source(pub):
    wf1 = Workflow(name='status')
    st1 = wf1.add_status('Status1', 'st1')
    display_form = st1.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [StringField(label='Test', data_source={'type': 'foobar'})]

    wf2 = Workflow(name='variables')
    wf2.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf2)
    wf2.variables_formdef.fields = [StringField(label='Test', data_source={'type': 'foobar'})]

    wf3 = Workflow(name='bo fields')
    wf3.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf3)
    wf3.backoffice_fields_formdef.fields = [
        StringField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_blah',
            data_source={'type': 'foobar'},
        )
    ]

    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        with pytest.raises(WorkflowImportError) as excinfo:
            Workflow.import_from_xml(io.BytesIO(export))
        assert excinfo.value.msg == 'Unknown referenced objects'
        assert excinfo.value.details == 'Unknown datasources: foobar'

    # carddef as datasource
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [StringField(id='1', label='Test', varname='foo')]
    carddef.store()

    display_form.formdef.fields[0].data_source = {'type': 'carddef:foo'}
    wf2.variables_formdef.fields[0].data_source = {'type': 'carddef:foo'}
    wf3.backoffice_fields_formdef.fields[0].data_source = {'type': 'carddef:foo'}

    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        Workflow.import_from_xml(io.BytesIO(export))

    display_form.formdef.fields[0].data_source = {'type': 'carddef:unknown'}
    wf2.variables_formdef.fields[0].data_source = {'type': 'carddef:unknown'}
    wf3.backoffice_fields_formdef.fields[0].data_source = {'type': 'carddef:unknown'}

    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        with pytest.raises(WorkflowImportError) as excinfo:
            Workflow.import_from_xml(io.BytesIO(export))
        assert excinfo.value.msg == 'Unknown referenced objects'
        assert excinfo.value.details == 'Unknown datasources: carddef:unknown'

    # carddef custom view as datasource
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    display_form.formdef.fields[0].data_source = {'type': 'carddef:foo:card-view'}
    wf2.variables_formdef.fields[0].data_source = {'type': 'carddef:foo:card-view'}
    wf3.backoffice_fields_formdef.fields[0].data_source = {'type': 'carddef:foo:card-view'}

    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        Workflow.import_from_xml(io.BytesIO(export))

    display_form.formdef.fields[0].data_source = {'type': 'carddef:foo:unknown'}
    wf2.variables_formdef.fields[0].data_source = {'type': 'carddef:foo:unknown'}
    wf3.backoffice_fields_formdef.fields[0].data_source = {'type': 'carddef:foo:unknown'}

    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        with pytest.raises(WorkflowImportError) as excinfo:
            Workflow.import_from_xml(io.BytesIO(export))
        assert excinfo.value.msg == 'Unknown referenced objects'
        assert excinfo.value.details == 'Unknown datasources: carddef:foo:unknown'


def test_workflow_with_block(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    wf1 = Workflow(name='status')
    st1 = wf1.add_status('Status1', 'st1')
    display_form = st1.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [BlockField(label='foo', block_slug='foobar')]

    wf2 = Workflow(name='variables')
    wf2.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf2)
    wf2.variables_formdef.fields = [BlockField(label='foo', block_slug='foobar')]

    wf3 = Workflow(name='bo fields')
    wf3.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf3)
    wf3.backoffice_fields_formdef.fields = [BlockField(label='foo', block_slug='foobar')]

    BlockDef.wipe()
    for wf in [wf1, wf2, wf3]:
        export = ET.tostring(export_to_indented_xml(wf))
        with pytest.raises(WorkflowImportError) as excinfo:
            Workflow.import_from_xml(io.BytesIO(export))
        assert excinfo.value.msg == 'Unknown referenced objects'
        assert excinfo.value.details == 'Unknown blocks of fields: foobar'


def test_workflow_with_category(pub):
    category = WorkflowCategory(name='test category')
    category.store()

    wf = Workflow(name='test category')
    wf.category_id = category.id
    wf.store()
    wf2 = assert_import_export_works(wf, include_id=True)
    assert wf2.category_id == wf.category_id

    # without id, lookup by slug
    wf2 = assert_import_export_works(wf, include_id=False)
    assert wf2.category_id == wf.category_id

    # without slug, fallback to category name
    xml_export_orig = ET.tostring(export_to_indented_xml(wf))
    xml_export = xml_export_orig.replace(b'slug="test-category"', b'')
    wf2 = Workflow.import_from_xml_tree(ET.parse(io.BytesIO(xml_export)))
    assert wf2.category_id == wf.category_id

    # import with non existing category
    WorkflowCategory.wipe()
    export = ET.tostring(wf.export_to_xml(include_id=True))
    wf3 = Workflow.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert wf3.category_id is None


def test_import_workflow_multiple_errors(pub):
    BlockDef.wipe()
    pub.cfg['sp'] = {'idp-manage-roles': True}

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    display_form = st1.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        BlockField(id='1', block_slug='foobar1'),
        BlockField(id='2', block_slug='foobaz1'),
        StringField(id='3', data_source={'type': 'foobar1'}),
        StringField(id='4', data_source={'type': 'carddef:unknown1'}),
        BoolField(id='5'),  # will have its type changed to foobazz1
    ]

    dispatch1 = st1.add_action('dispatch', id='_x')
    dispatch1.role_id = 'unknown-role1'
    dispatch1.role_key = 'plop'
    dispatch2 = st1.add_action('dispatch', id='_x2')
    dispatch2.role_id = 'unknown-role2'
    dispatch2.role_key = 'plop'

    item1 = st1.add_action('sendmail')
    item1.to = ['_receiver']
    item1.mail_template = 'unknown-mt-1'
    item2 = st1.add_action('sendmail')
    item2.to = ['_receiver']
    item2.mail_template = 'unknown-mt-2'

    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields = [
        BlockField(id='1', block_slug='foobar2'),
        BlockField(id='2', block_slug='foobaz2'),
        StringField(id='3', data_source={'type': 'foobar2'}),
        StringField(id='4', data_source={'type': 'carddef:unknown2'}),
        EmailField(id='5'),  # will have its type changed to foobazz2
    ]

    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        BlockField(id='1', block_slug='foobar3'),
        BlockField(id='2', block_slug='foobaz3'),
        StringField(id='3', data_source={'type': 'foobar3'}),
        StringField(id='4', data_source={'type': 'carddef:unknown3'}),
        ItemField(id='5'),  # will have its type changed to foobazz3
    ]

    export = (
        ET.tostring(export_to_indented_xml(wf))
        .replace(b'<type>bool</type>', b'<type>foobazz1</type>')
        .replace(b'<type>email</type>', b'<type>foobazz2</type>')
        .replace(b'<type>item</type>', b'<type>foobazz3</type>')
    )
    with pytest.raises(WorkflowImportError) as excinfo:
        Workflow.import_from_xml(io.BytesIO(export))
    assert excinfo.value.msg == 'Unknown referenced objects'
    assert excinfo.value.details == (
        'Unknown blocks of fields: foobar1, foobar2, foobar3, foobaz1, foobaz2, foobaz3; '
        'Unknown datasources: carddef:unknown1, carddef:unknown2, carddef:unknown3, foobar1, foobar2, foobar3; '
        'Unknown field types: foobazz1, foobazz2, foobazz3; '
        'Unknown mail templates: unknown-mt-1, unknown-mt-2; '
        'Unknown roles: unknown-role1, unknown-role2'
    )


def test_import_root_node_error():
    export = b'<wrong_root_node><name>Name</name></wrong_root_node>'
    with pytest.raises(WorkflowImportError) as excinfo:
        Workflow.import_from_xml(io.BytesIO(export))
    assert (
        excinfo.value.msg
        == 'Provided XML file is invalid, it starts with a <wrong_root_node> tag instead of <workflow>'
    )


def test_documentation_attributes(pub):
    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.documentation = 'doc1'
    status = workflow.add_status(name='New status')
    status.documentation = 'doc2'
    action = status.add_action('anonymise')
    action.documentation = 'doc3'
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.documentation = 'doc4'
    workflow.backoffice_fields_formdef.fields = [
        StringField(id='bo234', label='bo field 1'),
    ]
    workflow.backoffice_fields_formdef.fields[0].documentation = 'doc5'
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.documentation = 'doc6'
    workflow.variables_formdef.fields = [
        StringField(id='va123', label='var field 1'),
    ]
    workflow.variables_formdef.fields[0].documentation = 'doc7'
    global_action = workflow.add_global_action('action1')
    global_action.documentation = 'doc8'
    workflow.store()

    wf2 = assert_import_export_works(workflow)
    assert wf2.documentation == 'doc1'
    assert wf2.possible_status[0].documentation == 'doc2'
    assert wf2.possible_status[0].items[0].documentation == 'doc3'
    assert wf2.backoffice_fields_formdef.documentation == 'doc4'
    assert wf2.backoffice_fields_formdef.fields[0].documentation == 'doc5'
    assert wf2.variables_formdef.documentation == 'doc6'
    assert wf2.variables_formdef.fields[0].documentation == 'doc7'
    assert wf2.global_actions[0].documentation == 'doc8'
