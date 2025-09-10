import os
import re
import xml.etree.ElementTree as ET

import pytest
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.categories import BlockCategory, Category
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.testdef import TestDef, TestResults
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_role, create_superuser


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


def test_block_404(pub):
    create_superuser(pub)
    create_role(pub)
    BlockDef.wipe()
    app = login(get_app(pub))
    app.get('/backoffice/forms/blocks/1/', status=404)


def test_block_new(pub):
    create_superuser(pub)
    create_role(pub)
    BlockDef.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    resp = resp.click('Blocks of fields')
    resp = resp.click('New field block')
    resp.form['name'] = 'field block'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/forms/blocks/1/'
    resp = resp.follow()
    assert resp.pyquery('#appbar h2').text() == 'field block'
    assert 'There are not yet any fields' in resp

    resp.form['label'] = 'foobar'
    resp.form['type'] = 'string'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/forms/blocks/1/'
    resp = resp.follow()

    resp.form['label'] = 'barfoo'
    resp.form['type'] = 'string'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/forms/blocks/1/'
    resp = resp.follow()

    assert len(BlockDef.get(1).fields) == 2
    assert str(BlockDef.get(1).fields[0].id) != '1'  # don't use integers


def test_block_options(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^settings$'))
    assert 'readonly' not in resp.form['slug'].attrs
    resp.form['name'] = 'foo bar'
    resp = resp.form.submit('submit')
    assert BlockDef.get(block.id).name == 'foo bar'

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug=block.slug),
    ]
    formdef.store()

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^settings$'))
    assert 'readonly' in resp.form['slug'].attrs
    resp = resp.form.submit('cancel')
    resp = resp.follow()


def test_block_options_slug(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foo'
    block.fields = []
    block.store()

    block2 = BlockDef()
    block2.name = 'bar'
    block2.fields = []
    block2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/settings' % block.id)
    resp.form['slug'] = 'bar'
    resp = resp.form.submit('submit')
    assert 'This identifier is already used.' in resp.text

    resp = app.get('/backoffice/forms/blocks/%s/settings' % block.id)
    resp.form['slug'] = 'foo'
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/blocks/%s/settings' % block.id)
    resp.form['slug'] = 'foo2'
    resp = resp.form.submit('submit').follow()
    block.refresh_from_storage()
    assert block.slug == 'foo2'


def test_block_options_digest_template(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = []
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/settings' % block.id)
    resp.form['digest_template'] = 'X{{form_var_foo}}Y'
    resp = resp.form.submit('submit')
    assert (
        'Wrong variable &quot;form_var_…&quot; detected. Please replace it by &quot;block_var_…&quot;.'
        in resp.text
    )
    block = BlockDef.get(block.id)
    assert block.digest_template is None

    resp = app.get('/backoffice/forms/blocks/%s/settings' % block.id)
    resp.form['digest_template'] = 'X{{block_var_foo}}Y'
    resp = resp.form.submit('submit')
    block = BlockDef.get(block.id)
    assert block.digest_template == 'X{{block_var_foo}}Y'


def test_block_export_import(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^export$'))
    xml_export = resp.text
    xml_export_ok = resp.text

    resp = app.get('/backoffice/forms/blocks/')
    resp = resp.click(href='import')
    resp = resp.form.submit('cancel')  # shouldn't block on missing file
    resp = resp.follow()

    resp = resp.click(href='import')
    resp = resp.form.submit()
    assert 'ere were errors processing your form.' in resp

    resp.form['file'] = Upload('block', xml_export.encode('utf-8'))
    resp = resp.form.submit()
    resp = resp.follow()
    assert BlockDef.count() == 2

    new_blockdef = [x for x in BlockDef.select() if str(x.id) != str(block.id)][0]
    assert new_blockdef.name == 'Copy of foobar'
    assert new_blockdef.slug == 'foobar_1'
    assert len(new_blockdef.fields) == 1
    assert new_blockdef.fields[0].id == '123'

    resp = app.get('/backoffice/forms/blocks/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('block', xml_export.encode('utf-8'))
    resp = resp.form.submit()
    assert 'Copy of foobar (2)' in [x.name for x in BlockDef.select()]

    # import invalid content
    resp = app.get('/backoffice/forms/blocks/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('block', b'whatever')
    resp = resp.form.submit()
    assert 'Invalid File' in resp

    # unknown reference
    block.fields = [
        fields.StringField(id='1', data_source={'type': 'foobar'}),
    ]
    block.store()
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^export$'))
    xml_export = resp.text
    resp = app.get('/backoffice/forms/blocks/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('block', xml_export.encode('utf-8'))
    resp = resp.form.submit()
    assert 'Invalid File (Unknown referenced objects)' in resp
    assert '<ul><li>Unknown datasources: foobar</li></ul>' in resp

    # export with invalid file type
    resp = app.get('/backoffice/forms/blocks/')
    resp = resp.click(href='import')
    invalid_xml_export = xml_export_ok.replace('<type>string</type>', '<type>page</type>')
    resp.form['file'] = Upload('block', invalid_xml_export.encode('utf-8'))
    resp = resp.form.submit()
    assert 'Invalid field in XML file (page)' in resp


def test_block_delete(pub):
    create_superuser(pub)
    BlockDef.wipe()
    FormDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^delete$'))
    assert 'You are about to irrevocably delete this block.' in resp
    resp = resp.form.submit()
    resp = resp.follow()
    assert BlockDef.count() == 0

    # in use
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug=block.slug),
    ]
    formdef.store()
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^delete$'))
    assert 'This block is still used' in resp


def test_block_export_overwrite(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.post_conditions = [{'condition': {'type': 'django', 'value': 'True'}, 'error_message': 'oops'}]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^export$'))
    xml_export = resp.text

    block.slug = 'new-slug'
    block.name = 'New foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test bebore overwrite')]
    block.store()

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click('Overwrite')
    resp = resp.form.submit('cancel').follow()
    resp = resp.click('Overwrite')
    resp = resp.form.submit()
    assert 'There were errors processing your form.' in resp

    resp.form['file'] = Upload('block', xml_export.encode('utf-8'))
    resp = resp.form.submit()
    resp = resp.follow()
    assert BlockDef.count() == 1

    block.refresh_from_storage()
    assert block.fields[0].label == 'Test'
    assert block.post_conditions[0]['condition']['type'] == 'django'
    assert block.name == 'foobar'
    assert block.slug == 'new-slug'  # not overwritten

    # unknown reference
    block.fields = [fields.StringField(id='1', data_source={'type': 'foobar'})]
    block.store()
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^export$'))
    xml_export = resp.text
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click('Overwrite')
    resp.form['file'] = Upload('block', xml_export.encode('utf-8'))
    resp = resp.form.submit()
    assert 'Invalid File (Unknown referenced objects)' in resp
    assert '<ul><li>Unknown datasources: foobar</li></ul>' in resp


def test_block_edit_duplicate_delete_field(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('123/$'))
    resp.form['required'] = 'optional'
    resp.form['varname'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert BlockDef.get(block.id).fields[0].required == 'optional'
    assert BlockDef.get(block.id).fields[0].varname == 'test'

    resp = resp.click(href=re.compile('123/duplicate$'))
    resp = resp.follow()
    assert len(BlockDef.get(block.id).fields) == 2

    resp = resp.click(href='%s/delete' % BlockDef.get(block.id).fields[1].id)
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert len(BlockDef.get(block.id).fields) == 1


def test_block_use_in_formdef(pub):
    create_superuser(pub)
    FormDef.wipe()
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    resp.forms[0]['label'] = 'a block field'
    resp.forms[0]['type'] = 'block:foobar'
    resp = resp.forms[0].submit().follow()
    formdef.refresh_from_storage()
    assert 'a block field' in resp.text
    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)
    assert resp.pyquery('.field-edit--title').text() == 'a block field'
    assert resp.pyquery('.field-edit--subtitle').text() == 'Block of fields - foobar'
    assert resp.pyquery('.field-edit--subtitle a').attr.href.endswith(
        '/backoffice/forms/blocks/%s/' % block.id
    )
    assert resp.form['max_items$value_template'].value == '1'

    # check it's not possible to have an empty max_items
    resp.form['max_items$value_template'] = ''
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_max_items').text() == 'required field'

    # check there's no crash if block is missing
    block.remove_self()
    resp = app.get(formdef.get_admin_url() + 'fields/')
    assert resp.pyquery('#fields-list .type-block .type').text() == 'Block of fields (foobar, missing)'
    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)
    assert resp.pyquery('.field-edit--subtitle').text() == 'Block of fields (foobar, missing)'


def test_block_templates_for_counts(pub):
    create_superuser(pub)
    FormDef.wipe()
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    resp.forms[0]['label'] = 'a block field'
    resp.forms[0]['type'] = 'block:foobar'
    resp = resp.forms[0].submit().follow()
    formdef.refresh_from_storage()
    assert 'a block field' in resp.text
    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)

    # check plain strings are not ok
    resp.form['default_items_count$value_template'] = 'form_var_blah'
    resp.form['max_items$value_template'] = 'form_var_blah'
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_default_items_count').text() == 'value must be a number or a template'
    assert resp.pyquery('#form_error_max_items').text() == 'value must be a number or a template'

    # check templates are ok
    resp.form['default_items_count$value_template'] = '{{form_var_blah}}'
    resp.form['max_items$value_template'] = '{{form_var_blah}}'
    resp = resp.form.submit('submit').follow()
    formdef.refresh_from_storage()
    assert formdef.fields[0].default_items_count == '{{form_var_blah}}'
    assert formdef.fields[0].max_items == '{{form_var_blah}}'


def test_block_use_in_workflow_backoffice_fields(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    workflow = Workflow(name='test')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(workflow.get_admin_url())
    resp = resp.click(href='backoffice-fields/').follow()
    resp.forms[0]['label'] = 'a block field'
    resp.forms[0]['type'] = 'block:foobar'
    resp = resp.forms[0].submit().follow()
    workflow.refresh_from_storage()
    resp = resp.click(href=r'^%s/$' % workflow.backoffice_fields_formdef.fields[0].id)
    assert resp.form['max_items$value_template'].value == '1'


def test_blocks_category(pub):
    create_superuser(pub)

    BlockCategory.wipe()
    BlockDef.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/new')
    assert 'category_id' not in resp.form.fields

    block = BlockDef(name='foo')
    block.store()

    resp = app.get('/backoffice/forms/blocks/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a new category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert BlockCategory.count() == 1
    category = BlockCategory.select()[0]
    assert category.name == 'a new category'

    resp = app.get('/backoffice/forms/blocks/new')
    assert 'category_id' in resp.form.fields

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^settings$'))
    resp.forms[0]['category_id'] = str(category.id)
    resp = resp.forms[0].submit('cancel').follow()
    block.refresh_from_storage()
    assert block.category_id is None

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^settings$'))
    resp.forms[0]['category_id'] = str(category.id)
    resp = resp.forms[0].submit('submit').follow()
    block.refresh_from_storage()
    assert str(block.category_id) == str(category.id)

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^settings$'))
    assert resp.forms[0]['category_id'].value == str(category.id)

    resp = app.get('/backoffice/forms/blocks/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a second category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert BlockCategory.count() == 2
    category2 = [x for x in BlockCategory.select() if x.id != category.id][0]
    assert category2.name == 'a second category'

    app.get('/backoffice/forms/blocks/categories/update_order?order=%s;%s;' % (category2.id, category.id))
    categories = BlockCategory.select()
    BlockCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [str(category2.id), str(category.id)]

    app.get('/backoffice/forms/blocks/categories/update_order?order=%s;%s;0' % (category.id, category2.id))
    categories = BlockCategory.select()
    BlockCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [str(category.id), str(category2.id)]

    resp = app.get('/backoffice/forms/blocks/categories/')
    resp = resp.click('a new category')
    resp = resp.click('Delete')
    resp = resp.forms[0].submit()
    block.refresh_from_storage()
    assert not block.category_id


def test_removed_block_in_form_fields_list(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug='removed'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'Block of fields (removed, missing)' in resp.text


def test_block_edit_field_warnings(pub):
    create_superuser(pub)

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'ignore-hard-limits', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'block title'
    blockdef.fields = [fields.StringField(id='%d' % i, label='field %d' % i) for i in range(1, 10)]
    blockdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % blockdef.id)
    assert 'more than 20 fields' not in resp.text

    blockdef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(10, 31)])
    blockdef.store()
    resp = app.get('/backoffice/forms/blocks/%s/' % blockdef.id)
    assert 'more than 30 fields' not in resp.text
    assert resp.pyquery('#new-field')
    assert resp.pyquery('#fields-list a[title="Duplicate"]').length

    blockdef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(21, 51)])
    blockdef.store()
    resp = app.get('/backoffice/forms/blocks/%s/' % blockdef.id)
    assert 'This block of fields contains 60 fields.' in resp.text
    assert not resp.pyquery('#new-field')
    assert not resp.pyquery('#fields-list a[title="Duplicate"]').length


def test_block_edit_field_max_items(pub):
    create_superuser(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'block title'
    block.fields = [fields.StringField(id='123', label='field')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug=block.slug),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/{formdef.id}/fields/0/')
    resp.form['max_items$value_template'] = ''
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_max_items').text() == 'required field'
    resp.form['max_items$value_template'] = '{{ a = b }}'
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_max_items').text().startswith('syntax error in Django template')
    resp.form['max_items$value_template'] = 'plop'
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_max_items').text() == 'value must be a number or a template'
    resp.form['max_items$value_template'] = '10'
    resp = resp.form.submit('submit', status=302)
    formdef.refresh_from_storage()
    assert formdef.fields[0].max_items == '10'


def test_block_inspect(pub):
    create_superuser(pub)
    Workflow.wipe()
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='124', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='0', label='first test', block_slug=block.slug)]
    formdef.store()
    formdef = FormDef()
    formdef.name = 'form title 2'
    formdef.fields = [
        fields.BlockField(
            id='0', label='second test', block_slug=block.slug, max_items='3', remove_button=True
        )
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click('Inspector')
    assert resp.pyquery('#inspect-fields .inspect-field').length == 2
    assert '2 fields.' in resp.text
    assert resp.pyquery('table.block-usage tbody tr').length == 2
    assert 'second test 3 yes' in resp.pyquery('table.block-usage tbody tr td').text()
    assert 'first test 1 no' in resp.pyquery('table.block-usage tbody tr td').text()


def test_block_duplicate(pub):
    create_superuser(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'Foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='124', required='required', label='Test2'),
    ]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)

    resp = resp.click(href=re.compile('^duplicate$'))
    assert resp.form['name'].value == 'Foobar (copy)'
    resp = resp.form.submit('cancel').follow()
    assert BlockDef.count() == 1

    resp = resp.click(href=re.compile('^duplicate$'))
    assert resp.form['name'].value == 'Foobar (copy)'
    resp = resp.form.submit('submit').follow()
    assert BlockDef.count() == 2

    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('^duplicate$'))
    assert resp.form['name'].value == 'Foobar (copy 2)'
    resp.form['name'].value = 'other copy'
    resp = resp.form.submit('submit').follow()
    assert BlockDef.count() == 3
    assert {x.name for x in BlockDef.select()} == {'Foobar', 'Foobar (copy)', 'other copy'}
    assert {x.slug for x in BlockDef.select()} == {'foobar', 'foobar_copy', 'other_copy'}

    block_copy = BlockDef.get_by_slug('other_copy')
    assert len(block_copy.fields) == 2


def test_block_field_statistics_data_update(pub):
    create_superuser(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'Foobar'
    block.fields = [fields.BoolField(id='1', label='Bool', varname='bool')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug=block.slug),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['0'] = {'data': [{'1': True}]}
    formdata.store()

    assert 'bool' not in formdata.statistics_data

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/1/' % block.id)

    resp.form['display_locations$element3'] = True
    resp = resp.form.submit('submit').follow()
    assert 'Statistics data will be collected in the background.' in resp.text

    formdata.refresh_from_storage()
    assert formdata.statistics_data['bool'] == [True]


def test_block_test_results(pub):
    create_superuser(pub)
    TestDef.wipe()
    TestResults.wipe()
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug=block.slug),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/' % block.id)
    resp = resp.click(href=re.compile('123/$'))
    resp.form['varname'] = 'test'
    resp = resp.form.submit('submit').follow()
    assert TestResults.count() == 0

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'a'
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    resp = resp.click(href=re.compile('123/$'))
    resp.form['varname'] = 'test_3'
    resp = resp.form.submit('submit').follow()
    assert TestResults.count() == 1


def test_block_documentation(pub):
    create_superuser(pub)

    BlockDef.wipe()
    blockdef = FormDef()
    blockdef.name = 'block title'
    blockdef.fields = [fields.BoolField(id='1', label='Bool')]
    blockdef.store()

    app = login(get_app(pub))

    resp = app.get(blockdef.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(blockdef.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    blockdef.refresh_from_storage()
    assert blockdef.documentation == '<p>doc</p>'
    resp = app.get(blockdef.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(blockdef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation[hidden]')
    assert resp.pyquery('#sidebar[hidden]')
    resp = app.post_json(
        blockdef.get_admin_url() + 'fields/1/update-documentation', {'content': '<p>doc</p>'}
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    blockdef.refresh_from_storage()
    assert blockdef.fields[0].documentation == '<p>doc</p>'
    resp = app.get(blockdef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation:not([hidden])')
    assert resp.pyquery('#sidebar:not([hidden])')


def test_block_options_post_conditions(pub):
    create_superuser(pub)
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/forms/blocks/{block.id}/settings')
    resp.form['post_conditions$element0$condition$value_django'] = 'condition_1'
    resp.form['post_conditions$element0$error_message$value_template'] = 'error 1'
    resp = resp.form.submit('post_conditions$add_element')
    resp.form['post_conditions$element1$condition$value_django'] = 'condition_2'
    resp.form['post_conditions$element1$error_message$value_template'] = 'error 2'
    resp = resp.form.submit('submit')
    block.refresh_from_storage()
    assert block.post_conditions == [
        {'condition': {'type': 'django', 'value': 'condition_1'}, 'error_message': 'error 1'},
        {'condition': {'type': 'django', 'value': 'condition_2'}, 'error_message': 'error 2'},
    ]


def test_block_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/forms/', status=403)

    BlockCategory.wipe()
    Category.wipe()

    form_cat = Category(name='formcat')
    form_cat.management_roles = [backoffice_role]
    form_cat.store()
    app.get('/backoffice/forms/', status=200)
    app.get('/backoffice/forms/blocks/', status=403)

    cat = BlockCategory(name='Foo')
    cat.store()

    resp = app.get('/backoffice/studio/', status=200)
    resp.click('Forms', index=0)
    with pytest.raises(IndexError):
        resp.click('Block of fields', index=0)
    app.get('/backoffice/forms/blocks/', status=403)

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'block title'
    blockdef.category_id = cat.id
    blockdef.fields = []
    blockdef.store()

    cat = BlockCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/forms/blocks/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'block title' not in resp.text  # block in that category
    assert 'Bar' not in resp.text  # not yet any block in this category

    resp = resp.click('New field block')
    resp.forms[0]['name'] = 'block in category'
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user
    resp = resp.forms[0].submit().follow()
    BlockDef.get_by_slug('block-in-category')

    # check category select only let choose one
    resp = resp.click(href='settings')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user

    resp = app.get('/backoffice/forms/blocks/')
    assert 'Bar' in resp.text  # now there's a block in this category
    assert 'block in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    app.get('/backoffice/forms/blocks/categories/', status=403)

    # no import into other category
    blockdef_xml = ET.tostring(blockdef.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('blockdef.wcs', blockdef_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    resp = app.get('/backoffice/studio/', status=200)
    resp.click('Forms', index=0)
    resp.click('Blocks of fields', index=0)

    # check "Block of fields" button is present in /admin/forms/
    resp = resp.click('Forms', index=0)
    resp.click('Blocks of fields', index=0)


def test_blocks_by_slug(pub):
    BlockDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    blockdef = BlockDef()
    blockdef.name = 'block title'
    blockdef.store()

    assert app.get('/backoffice/forms/blocks/by-slug/block_title').location == blockdef.get_admin_url()
    assert app.get('/backoffice/forms/blocks/by-slug/xxx', status=404)
