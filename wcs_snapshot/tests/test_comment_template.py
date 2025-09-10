import io
import os
import re
import xml.etree.ElementTree as ET

import pytest
from quixote import cleanup
from webtest import Upload

from wcs.categories import CommentTemplateCategory, WorkflowCategory
from wcs.comment_templates import CommentTemplate
from wcs.fields import FileField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.upload_storage import PicklableUpload
from wcs.workflows import Workflow

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    return pub


@pytest.fixture
def superuser(pub):
    if pub.user_class.select(lambda x: x.name == 'admin'):
        user1 = pub.user_class.select(lambda x: x.name == 'admin')[0]
        user1.is_admin = True
        user1.store()
        return user1

    user1 = pub.user_class(name='admin')
    user1.is_admin = True
    user1.store()

    account1 = PasswordAccount(id='admin')
    account1.set_password('admin')
    account1.user_id = user1.id
    account1.store()

    return user1


@pytest.fixture
def comment_template():
    CommentTemplate.wipe()
    comment_template = CommentTemplate(name='test CT')
    comment_template.comment = 'test comment'
    comment_template.store()
    return comment_template


def test_comment_templates_basics(pub, superuser):
    CommentTemplateCategory.wipe()
    CommentTemplate.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    assert 'Comment Templates' in resp
    resp = resp.click('Comment Templates')
    assert 'There are no comment templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first comment template'
    resp = resp.form.submit('cancel').follow()
    assert 'There are no comment templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first comment template'
    resp = resp.form.submit('submit').follow()
    resp.form['comment'] = 'comment body'
    resp = resp.form.submit('submit').follow()

    resp = resp.click('Edit')
    resp.form['comment'] = 'edited comment body'
    resp.form['attachments$element0'] = 'plop'
    resp = resp.form.submit('submit').follow()

    resp = resp.click('Edit')
    assert resp.form['comment'].value == 'edited comment body'
    assert resp.form['attachments$element0'].value == 'plop'
    resp = resp.form.submit('submit').follow()

    resp = resp.click('Delete')
    resp = resp.form.submit('cancel').follow()
    assert 'first comment template' in resp

    resp = resp.click('Delete')
    resp = resp.form.submit('submit').follow()
    assert 'first comment template' not in resp
    assert 'There are no comment templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first comment template'
    resp = resp.form.submit('submit').follow()
    resp.form['comment'] = 'comment body'
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Comment Templates')
    assert 'first comment template' in resp


def test_comment_template_in_use(pub, superuser):
    Workflow.wipe()
    CommentTemplate.wipe()
    workflow = Workflow(name='test workflow')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('register-comment')
    item.comment = 'Hello'
    workflow.store()

    comment_template = CommentTemplate(name='test comment template')
    comment_template.comment = 'test comment'
    comment_template.store()
    assert comment_template.is_in_use() is False

    item.comment_template = comment_template.slug
    workflow.store()
    assert comment_template.is_in_use() is True

    # check workflow usage is displayed
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/comment-templates/%s/' % comment_template.id)
    usage_url = resp.pyquery('[data-async-url]').attr['data-async-url']
    resp = app.get(usage_url)
    assert resp.pyquery('a').attr.href == item.get_admin_url()
    assert resp.pyquery('a').text() == 'test workflow - Status1 - History Message (to everybody)'
    resp.click('test workflow')  # make sure the link is ok

    resp = app.get('/backoffice/workflows/comment-templates/%s/' % comment_template.id)
    resp = resp.click('Delete')
    assert 'still used' in resp.text

    item.comment_template = None
    workflow.store()
    resp = app.get(usage_url)
    assert 'No usage detected.' in resp.text


def test_admin_workflow_edit(pub, superuser):
    CommentTemplateCategory.wipe()
    Workflow.wipe()
    CommentTemplate.wipe()
    comment_template = CommentTemplate(name='test comment template')
    comment_template.comment = 'test comment'
    comment_template.store()

    workflow = Workflow(name='test comment template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('register-comment')
    item.comment = 'Hello'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st1.id))
    assert [o[0] for o in resp.form['comment_template'].options] == ['', 'test-comment-template']

    cat_b = CommentTemplateCategory(name='Cat B')
    cat_b.store()
    comment_template = CommentTemplate(name='foo bar')
    comment_template.category_id = cat_b.id
    comment_template.store()
    comment_template = CommentTemplate(name='bar foo')
    comment_template.category_id = cat_b.id
    comment_template.store()
    cat_a = CommentTemplateCategory(name='Cat A')
    cat_a.store()
    comment_template = CommentTemplate(name='foo baz')
    comment_template.category_id = cat_a.id
    comment_template.store()

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st1.id))
    assert [o[0] for o in resp.form['comment_template'].options] == [
        '',
        'foo-baz',
        'bar-foo',
        'foo-bar',
        'test-comment-template',
    ]
    resp.form['comment_template'] = 'test-comment-template'
    resp = resp.form.submit('submit')

    workflow = Workflow.get(workflow.id)
    assert workflow.possible_status[0].items[0].comment_template == 'test-comment-template'


def test_comment_templates_category(pub, superuser):
    CommentTemplateCategory.wipe()
    CommentTemplate.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/comment-templates/new')
    assert 'category_id' not in resp.form.fields

    comment_template = CommentTemplate(name='foo')
    comment_template.store()

    resp = app.get('/backoffice/workflows/comment-templates/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a new category'
    resp.form['description'] = 'description of the category'
    resp = resp.form.submit('submit')
    assert CommentTemplateCategory.count() == 1
    category = CommentTemplateCategory.select()[0]
    assert category.name == 'a new category'

    resp = app.get('/backoffice/workflows/comment-templates/new')
    resp.form['name'] = 'template 2'
    resp = resp.form.submit('submit').follow()
    assert CommentTemplate.count() == 2
    assert CommentTemplate.get(2).category_id is None
    resp = app.get('/backoffice/workflows/comment-templates/new')
    resp.form['name'] = 'template 3'
    resp.form['category_id'] = str(category.id)
    resp = resp.form.submit('submit').follow()
    assert CommentTemplate.count() == 3
    assert CommentTemplate.get(3).category_id == str(category.id)

    resp = app.get('/backoffice/workflows/comment-templates/%s/' % comment_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp = resp.form.submit('cancel').follow()
    comment_template.refresh_from_storage()
    assert comment_template.category_id is None

    resp = app.get('/backoffice/workflows/comment-templates/%s/' % comment_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp.form['comment'] = 'comment body'
    resp = resp.form.submit('submit').follow()
    comment_template.refresh_from_storage()
    assert str(comment_template.category_id) == str(category.id)

    resp = app.get('/backoffice/workflows/comment-templates/%s/' % comment_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    assert resp.form['category_id'].value == str(category.id)

    resp = app.get('/backoffice/workflows/comment-templates/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a second category'
    resp.form['description'] = 'description of the category'
    resp = resp.form.submit('submit')
    assert CommentTemplateCategory.count() == 2
    category2 = [x for x in CommentTemplateCategory.select() if x.id != category.id][0]
    assert category2.name == 'a second category'

    app.get(
        '/backoffice/workflows/comment-templates/categories/update_order?order=%s;%s;'
        % (category2.id, category.id)
    )
    categories = CommentTemplateCategory.select()
    CommentTemplateCategory.sort_by_position(categories)
    assert [str(x.id) for x in categories] == [str(category2.id), str(category.id)]

    app.get(
        '/backoffice/workflows/comment-templates/categories/update_order?order=%s;%s;0'
        % (category.id, category2.id)
    )
    categories = CommentTemplateCategory.select()
    CommentTemplateCategory.sort_by_position(categories)
    assert [str(x.id) for x in categories] == [str(category.id), str(category2.id)]

    resp = app.get('/backoffice/workflows/comment-templates/categories/')
    resp = resp.click('a new category')
    resp = resp.click('Delete')
    resp = resp.form.submit()
    comment_template.refresh_from_storage()
    assert not comment_template.category_id


def test_workflow_register_comment_template(pub):
    Workflow.wipe()
    CommentTemplate.wipe()

    comment_template = CommentTemplate(name='test comment template')
    comment_template.comment = 'test comment'
    comment_template.store()

    workflow = Workflow(name='test comment template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('register-comment')
    item.comment = 'Hello'
    item.comment_template = comment_template.slug
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item.perform(formdata)
    assert len(formdata.evolution) == 1
    assert len(formdata.evolution[0].parts) == 2
    assert formdata.evolution[-1].parts[1].content == '<p>test comment</p>'

    # check nothing is registered and an error is logged if the comment template is missing
    CommentTemplate.wipe()
    item.perform(formdata)
    assert len(formdata.evolution) == 1
    assert len(formdata.evolution[0].parts) == 2
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary
        == 'reference to invalid comment template test-comment-template in status Status1'
    )


def test_workflow_register_comment_template_attachments(pub):
    Workflow.wipe()
    CommentTemplate.wipe()

    comment_template = CommentTemplate(name='test comment template')
    comment_template.comment = 'test comment'
    comment_template.attachments = ['{{ form_var_file1_raw }}']
    comment_template.store()

    workflow = Workflow(name='test comment template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('register-comment')
    item.comment = 'Hello'
    item.comment_template = comment_template.slug
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='1', label='File', varname='file1'),
    ]
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata = formdef.data_class()()
    formdata.data = {'1': upload}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item.perform(formdata)
    assert len(formdata.evolution) == 1
    assert len(formdata.evolution[0].parts) == 3
    assert formdata.evolution[-1].parts[2].content == '<p>test comment</p>'
    assert formdata.evolution[-1].parts[1].base_filename == 'test.jpeg'

    # check two files are sent if attachments are also defined on the action itself.
    item.attachments = ['{{ form_var_file1_raw }}']
    item.perform(formdata)
    assert len(formdata.evolution) == 1
    assert len(formdata.evolution[0].parts) == 6
    assert formdata.evolution[-1].parts[5].content == '<p>test comment</p>'
    assert formdata.evolution[-1].parts[4].base_filename == 'test.jpeg'
    assert formdata.evolution[-1].parts[3].base_filename == 'test.jpeg'


def test_workflow_register_comment_template_empty(pub):
    Workflow.wipe()
    CommentTemplate.wipe()

    comment_template = CommentTemplate(name='test comment template')
    comment_template.comment = None
    comment_template.store()

    workflow = Workflow(name='test comment template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('register-comment')
    item.comment = 'Hello'
    item.comment_template = comment_template.slug
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item.perform(formdata)
    assert len(formdata.evolution) == 1
    assert len(formdata.evolution[0].parts) == 1


def test_comment_templates_export(pub, superuser, comment_template):
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/comment-templates/1/')

    resp = resp.click(href='export')
    xml_export = resp.text

    ds = io.StringIO(xml_export)
    comment_template2 = CommentTemplate.import_from_xml(ds)
    assert comment_template2.name == 'test CT'


def test_comment_templates_import(pub, superuser, comment_template):
    comment_template.slug = 'foobar'
    comment_template.store()
    comment_template_xml = ET.tostring(comment_template.export_to_xml(include_id=True))
    CommentTemplate.wipe()
    assert CommentTemplate.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/comment-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('comment_template.wcs', comment_template_xml)
    resp = resp.forms[0].submit()
    assert CommentTemplate.count() == 1
    assert {wc.slug for wc in CommentTemplate.select()} == {'foobar'}

    # check slug
    resp = app.get('/backoffice/workflows/comment-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('comment_template.wcs', comment_template_xml)
    resp = resp.forms[0].submit()
    assert CommentTemplate.count() == 2
    assert {wc.slug for wc in CommentTemplate.select()} == {'foobar', 'test-ct'}
    resp = app.get('/backoffice/workflows/comment-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('comment_template.wcs', comment_template_xml)
    resp = resp.forms[0].submit()
    assert CommentTemplate.count() == 3
    assert {wc.slug for wc in CommentTemplate.select()} == {'foobar', 'test-ct', 'test-ct-1'}

    # import an invalid file
    resp = app.get('/backoffice/workflows/comment-templates/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('comment_template.wcs', b'garbage')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text


def test_comment_templates_duplicate(pub, superuser, comment_template):
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/comment-templates/1/')

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test CT (copy)'
    resp = resp.form.submit('cancel').follow()
    assert CommentTemplate.count() == 1

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test CT (copy)'
    resp = resp.form.submit('submit').follow()
    assert CommentTemplate.count() == 2

    resp = app.get('/backoffice/workflows/comment-templates/1/')
    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test CT (copy 2)'
    resp.form['name'].value = 'other copy'
    resp = resp.form.submit('submit').follow()
    assert CommentTemplate.count() == 3
    assert {x.name for x in CommentTemplate.select()} == {'test CT', 'test CT (copy)', 'other copy'}
    assert {x.slug for x in CommentTemplate.select()} == {'test-ct', 'test-ct-copy', 'other-copy'}


def export_to_indented_xml(comment_template, include_id=False):
    comment_template_xml = comment_template.export_to_xml(include_id=include_id)
    ET.indent(comment_template_xml)
    return comment_template_xml


def assert_import_export_works(comment_template, include_id=False):
    comment_template2 = CommentTemplate.import_from_xml_tree(
        ET.fromstring(ET.tostring(comment_template.export_to_xml(include_id))), include_id
    )
    assert ET.tostring(export_to_indented_xml(comment_template)) == ET.tostring(
        export_to_indented_xml(comment_template2)
    )
    return comment_template2


def test_comment_template(pub):
    comment_template = CommentTemplate(name='test')
    assert_import_export_works(comment_template, include_id=True)


def test_comment_template_with_category(pub):
    category = CommentTemplateCategory(name='test category')
    category.store()

    comment_template = CommentTemplate(name='test category')
    comment_template.category_id = category.id
    comment_template.store()
    comment_template2 = assert_import_export_works(comment_template, include_id=True)
    assert str(comment_template2.category_id) == str(comment_template.category_id)

    # import with non existing category
    CommentTemplateCategory.wipe()
    export = ET.tostring(comment_template.export_to_xml(include_id=True))
    comment_template3 = CommentTemplate.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert comment_template3.category_id is None


def test_comment_template_migration(pub):
    comment_template = CommentTemplate(name='test template')
    comment_template.description = 'hello'
    assert comment_template.migrate() is True
    assert not comment_template.description
    assert comment_template.documentation == 'hello'


def test_comment_template_legacy_xml(pub):
    comment_template = CommentTemplate(name='test template')
    comment_template.documentation = 'hello'
    export = ET.tostring(export_to_indented_xml(comment_template))
    export = export.replace(b'documentation>', b'description>')

    comment_template2 = CommentTemplate.import_from_xml_tree(ET.fromstring(export))
    comment_template2.store()
    comment_template2.refresh_from_storage()
    assert comment_template2.documentation


def test_comment_template_documentation(pub, superuser):
    CommentTemplate.wipe()
    comment_template = CommentTemplate(name='foobar')
    comment_template.store()

    app = login(get_app(pub))

    resp = app.get(comment_template.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(comment_template.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    comment_template.refresh_from_storage()
    assert comment_template.documentation == '<p>doc</p>'
    resp = app.get(comment_template.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')


def test_comment_template_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/workflows/', status=403)

    CommentTemplateCategory.wipe()
    WorkflowCategory.wipe()

    wf_cat = WorkflowCategory(name='wfcat')
    wf_cat.management_roles = [backoffice_role]
    wf_cat.store()
    app.get('/backoffice/workflows/', status=200)
    app.get('/backoffice/workflows/comment-templates/', status=403)

    cat = CommentTemplateCategory(name='Foo')
    cat.store()

    app.get('/backoffice/workflows/comment-templates/', status=403)

    CommentTemplate.wipe()
    comment_template = CommentTemplate(name='comment template title')
    comment_template.comment = 'test body'
    comment_template.category_id = cat.id
    comment_template.store()

    comment_template2 = CommentTemplate(name='comment2 template title')  # no category
    comment_template2.comment = 'test body'
    comment_template2.store()

    cat = CommentTemplateCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/workflows/comment-templates/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'comment template title' not in resp.text  # comment template in that category
    assert 'Bar' not in resp.text  # not yet any comment template in this category

    app.get(comment_template.get_admin_url(), status=403)
    app.get(comment_template2.get_admin_url(), status=403)

    resp = resp.click('New comment template')
    resp.form['name'] = 'comment template in category'
    assert len(resp.form['category_id'].options) == 1  # single option
    assert resp.form['category_id'].value == str(cat.id)  # the category managed by user
    resp = resp.form.submit('submit').follow()
    mt = CommentTemplate.get_by_slug('comment-template-in-category')

    # check category select only let choose one
    resp = app.get(mt.get_admin_url())
    resp = resp.click(href='edit')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == str(cat.id)  # the category managed by user

    resp = app.get('/backoffice/workflows/comment-templates/')
    assert 'Bar' in resp.text  # now there's a data source in this category
    assert 'comment template in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    app.get('/backoffice/workflows/comment-templates/categories/', status=403)

    # no import into other category
    comment_template_xml = ET.tostring(comment_template.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('comment_template.wcs', comment_template_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    resp = app.get('/backoffice/studio/', status=200)
    resp.click('Workflows', index=0)
    resp.click('Comment templates', index=0)
    with pytest.raises(IndexError):
        resp.click('Mail templates', index=0)
    with pytest.raises(IndexError):
        resp.click('Data sources', index=0)
