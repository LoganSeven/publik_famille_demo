import io
import os
import re
import xml.etree.ElementTree as ET

import pytest
from quixote import cleanup
from webtest import Upload

from wcs.categories import MailTemplateCategory, WorkflowCategory
from wcs.fields import FileField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.mail_templates import MailTemplate
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
def mail_template():
    MailTemplate.wipe()
    mail_template = MailTemplate(name='test MT')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()
    return mail_template


def test_mail_templates_basics(pub, superuser):
    MailTemplateCategory.wipe()
    MailTemplate.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    assert 'Mail Templates' in resp
    resp = resp.click('Mail Templates')
    assert 'There are no mail templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first mail template'
    resp = resp.form.submit('cancel').follow()
    assert 'There are no mail templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first mail template'
    resp = resp.form.submit('submit').follow()
    resp.form['subject'] = 'mail subject'
    resp.form['body'] = 'mail body'
    resp = resp.form.submit('submit').follow()

    assert not resp.pyquery('.mail-attachments')

    resp = resp.click('Edit')
    resp.form['subject'] = 'edited mail subject'
    resp.form['body'] = 'edited mail body'
    resp.form['attachments$element0'] = 'plop'
    resp = resp.form.submit('submit').follow()

    resp = resp.click('Edit')
    assert resp.form['subject'].value == 'edited mail subject'
    assert resp.form['body'].value == 'edited mail body'
    assert resp.form['attachments$element0'].value == 'plop'
    resp = resp.form.submit('submit').follow()

    assert resp.pyquery('.mail-attachments')
    assert 'plop' in resp.text

    resp = resp.click('Delete')
    resp = resp.form.submit('cancel').follow()
    assert 'first mail template' in resp

    resp = resp.click('Delete')
    resp = resp.form.submit('submit').follow()
    assert 'first mail template' not in resp
    assert 'There are no mail templates defined.' in resp

    resp = resp.click('New')
    resp.form['name'] = 'first mail template'
    resp = resp.form.submit('submit').follow()
    resp.form['subject'] = 'mail subject'
    resp.form['body'] = 'mail body'
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Mail Templates')
    assert 'first mail template' in resp


def test_mail_template_in_use(pub, superuser):
    Workflow.wipe()
    MailTemplate.wipe()
    workflow = Workflow(name='test workflow')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = ['_receiver']
    item.subject = 'Foobar'
    item.body = 'Hello'
    workflow.store()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()
    assert mail_template.is_in_use() is False

    item.mail_template = mail_template.slug
    workflow.store()
    assert mail_template.is_in_use() is True

    # check workflow usage is displayed
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mail_template.id)
    usage_url = resp.pyquery('[data-async-url]').attr['data-async-url']
    resp = app.get(usage_url)
    assert resp.pyquery('a').attr.href == item.get_admin_url()
    assert resp.pyquery('a').text() == 'test workflow - Status1 - Email (to Recipient)'
    resp.click('test workflow')  # make sure the link is ok

    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mail_template.id)
    resp = resp.click('Delete')
    assert 'still used' in resp.text

    item.mail_template = None
    workflow.store()
    resp = app.get(usage_url)
    assert 'No usage detected.' in resp.text


def test_admin_workflow_edit(pub, superuser):
    MailTemplateCategory.wipe()
    Workflow.wipe()
    MailTemplate.wipe()
    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()

    workflow = Workflow(name='test mail template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = ['_receiver']
    item.subject = 'Foobar'
    item.body = 'Hello'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st1.id))
    assert [o[0] for o in resp.form['mail_template'].options] == ['', 'test-mail-template']

    cat_b = MailTemplateCategory(name='Cat B')
    cat_b.store()
    mail_template = MailTemplate(name='foo bar')
    mail_template.category_id = cat_b.id
    mail_template.store()
    mail_template = MailTemplate(name='bar foo')
    mail_template.category_id = cat_b.id
    mail_template.store()
    cat_a = MailTemplateCategory(name='Cat A')
    cat_a.store()
    mail_template = MailTemplate(name='foo baz')
    mail_template.category_id = cat_a.id
    mail_template.store()

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st1.id))
    assert [o[0] for o in resp.form['mail_template'].options] == [
        '',
        'foo-baz',
        'bar-foo',
        'foo-bar',
        'test-mail-template',
    ]
    resp.form['mail_template'] = 'test-mail-template'
    resp = resp.form.submit('submit')

    workflow = Workflow.get(workflow.id)
    assert workflow.possible_status[0].items[0].mail_template == 'test-mail-template'


def test_mail_templates_category(pub, superuser):
    MailTemplateCategory.wipe()
    MailTemplate.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/mail-templates/new')
    assert 'category_id' not in resp.form.fields

    mail_template = MailTemplate(name='foo')
    mail_template.store()

    resp = app.get('/backoffice/workflows/mail-templates/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a new category'
    resp.form['description'] = 'description of the category'
    resp = resp.form.submit('submit')
    assert MailTemplateCategory.count() == 1
    category = MailTemplateCategory.select()[0]
    assert category.name == 'a new category'

    resp = app.get('/backoffice/workflows/mail-templates/new')
    resp.form['name'] = 'template 2'
    resp = resp.form.submit('submit').follow()
    assert MailTemplate.count() == 2
    assert MailTemplate.get(2).category_id is None
    resp = app.get('/backoffice/workflows/mail-templates/new')
    resp.form['name'] = 'template 3'
    resp.form['category_id'] = str(category.id)
    resp = resp.form.submit('submit').follow()
    assert MailTemplate.count() == 3
    assert MailTemplate.get(3).category_id == str(category.id)

    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mail_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp = resp.form.submit('cancel').follow()
    mail_template.refresh_from_storage()
    assert mail_template.category_id is None

    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mail_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp.form['subject'] = 'mail subject'
    resp.form['body'] = 'mail body'
    resp = resp.form.submit('submit').follow()
    mail_template.refresh_from_storage()
    assert str(mail_template.category_id) == str(category.id)

    resp = app.get('/backoffice/workflows/mail-templates/%s/' % mail_template.id)
    resp = resp.click(href=re.compile('^edit$'))
    assert resp.form['category_id'].value == str(category.id)

    resp = app.get('/backoffice/workflows/mail-templates/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a second category'
    resp.form['description'] = 'description of the category'
    resp = resp.form.submit('submit')
    assert MailTemplateCategory.count() == 2
    category2 = [x for x in MailTemplateCategory.select() if x.id != category.id][0]
    assert category2.name == 'a second category'

    app.get(
        '/backoffice/workflows/mail-templates/categories/update_order?order=%s;%s;'
        % (category2.id, category.id)
    )
    categories = MailTemplateCategory.select()
    MailTemplateCategory.sort_by_position(categories)
    assert [str(x.id) for x in categories] == [str(category2.id), str(category.id)]

    app.get(
        '/backoffice/workflows/mail-templates/categories/update_order?order=%s;%s;0'
        % (category.id, category2.id)
    )
    categories = MailTemplateCategory.select()
    MailTemplateCategory.sort_by_position(categories)
    assert [str(x.id) for x in categories] == [str(category.id), str(category2.id)]

    resp = app.get('/backoffice/workflows/mail-templates/categories/')
    resp = resp.click('a new category')
    resp = resp.click('Delete')
    resp = resp.form.submit()
    mail_template.refresh_from_storage()
    assert not mail_template.category_id


def test_workflow_send_mail_template_with_sql(superuser, emails):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)

    Workflow.wipe()
    MailTemplate.wipe()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()

    workflow = Workflow(name='test mail template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = 'xyz@localhost'
    item.subject = 'Foobar'
    item.body = 'Hello'
    item.mail_template = mail_template.slug
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('test subject')['email_rcpt'] == ['xyz@localhost']
    assert 'test body' in emails.get('test subject')['msg'].get_payload(0).get_payload()

    # check nothing is sent and an error is logged if the mail template is
    # missing
    emails.empty()
    MailTemplate.wipe()
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 0
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'reference to invalid mail template test-mail-template in status Status1'


def test_workflow_send_mail_template_attachments(pub, superuser, emails):
    Workflow.wipe()
    MailTemplate.wipe()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.attachments = ['{{ form_var_file1_raw }}']
    mail_template.store()

    workflow = Workflow(name='test mail template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = 'xyz@localhost'
    item.subject = 'Foobar'
    item.body = 'Hello'
    item.mail_template = mail_template.slug
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
    pub.process_after_jobs()
    assert emails.count() == 1
    payloads = emails.get('test subject')['msg'].get_payload()
    assert len(payloads) == 2
    assert payloads[1].get_content_type() == 'image/jpeg'

    # check two files are sent if mail attachments are also defined on the
    # action itself.
    emails.empty()
    item.attachments = ['{{ "test"|qrcode }}']
    item.perform(formdata)
    pub.process_after_jobs()
    payloads = emails.get('test subject')['msg'].get_payload()
    assert len(payloads) == 3
    assert payloads[1].get_content_type() == 'image/png'
    assert payloads[2].get_content_type() == 'image/jpeg'

    # check duplicated files are not sent
    emails.empty()
    item.attachments = ['{{ form_var_file1_raw }}']
    item.perform(formdata)
    pub.process_after_jobs()
    payloads = emails.get('test subject')['msg'].get_payload()
    assert len(payloads) == 2
    assert payloads[1].get_content_type() == 'image/jpeg'


def test_workflow_send_mail_template_empty(pub, superuser, emails):
    Workflow.wipe()
    MailTemplate.wipe()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = None
    mail_template.store()

    workflow = Workflow(name='test mail template')
    st1 = workflow.add_status('Status1')
    item = st1.add_action('sendmail')
    item.to = 'xyz@localhost'
    item.subject = 'Foobar'
    item.body = 'Hello'
    item.mail_template = mail_template.slug
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
    pub.process_after_jobs()
    assert emails.count() == 0


def test_mail_templates_export(pub, superuser, mail_template):
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/mail-templates/1/')

    resp = resp.click(href='export')
    xml_export = resp.text

    ds = io.StringIO(xml_export)
    mail_template2 = MailTemplate.import_from_xml(ds)
    assert mail_template2.name == 'test MT'


def test_mail_templates_import(pub, superuser, mail_template):
    mail_template.slug = 'foobar'
    mail_template.store()
    mail_template_xml = ET.tostring(mail_template.export_to_xml(include_id=True))
    MailTemplate.wipe()
    assert MailTemplate.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/mail-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('mail_template.wcs', mail_template_xml)
    resp = resp.forms[0].submit()
    assert MailTemplate.count() == 1
    assert {wc.slug for wc in MailTemplate.select()} == {'foobar'}

    # check slug
    resp = app.get('/backoffice/workflows/mail-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('mail_template.wcs', mail_template_xml)
    resp = resp.forms[0].submit()
    assert MailTemplate.count() == 2
    assert {wc.slug for wc in MailTemplate.select()} == {'foobar', 'test-mt'}
    resp = app.get('/backoffice/workflows/mail-templates/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('mail_template.wcs', mail_template_xml)
    resp = resp.forms[0].submit()
    assert MailTemplate.count() == 3
    assert {wc.slug for wc in MailTemplate.select()} == {'foobar', 'test-mt', 'test-mt-1'}

    # import an invalid file
    resp = app.get('/backoffice/workflows/mail-templates/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('mail_template.wcs', b'garbage')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text


def test_mail_templates_duplicate(pub, superuser, mail_template):
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/mail-templates/1/')

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test MT (copy)'
    resp = resp.form.submit('cancel').follow()
    assert MailTemplate.count() == 1

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test MT (copy)'
    resp = resp.form.submit('submit').follow()
    assert MailTemplate.count() == 2

    resp = app.get('/backoffice/workflows/mail-templates/1/')
    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'test MT (copy 2)'
    resp.form['name'].value = 'other copy'
    resp = resp.form.submit('submit').follow()
    assert MailTemplate.count() == 3
    assert {x.name for x in MailTemplate.select()} == {'test MT', 'test MT (copy)', 'other copy'}
    assert {x.slug for x in MailTemplate.select()} == {'test-mt', 'test-mt-copy', 'other-copy'}


def export_to_indented_xml(mail_template, include_id=False):
    mail_template_xml = mail_template.export_to_xml(include_id=include_id)
    ET.indent(mail_template_xml)
    return mail_template_xml


def assert_import_export_works(mail_template, include_id=False):
    mail_template2 = MailTemplate.import_from_xml_tree(
        ET.fromstring(ET.tostring(mail_template.export_to_xml(include_id))), include_id
    )
    assert ET.tostring(export_to_indented_xml(mail_template)) == ET.tostring(
        export_to_indented_xml(mail_template2)
    )
    return mail_template2


def test_mail_template(pub):
    mail_template = MailTemplate(name='test')
    assert_import_export_works(mail_template, include_id=True)


def test_mail_template_with_category(pub):
    category = MailTemplateCategory(name='test category')
    category.store()

    mail_template = MailTemplate(name='test category')
    mail_template.category_id = str(category.id)
    mail_template.store()
    mail_template2 = assert_import_export_works(mail_template, include_id=True)
    assert mail_template2.category_id == mail_template.category_id

    # import with non existing category
    MailTemplateCategory.wipe()
    export = ET.tostring(mail_template.export_to_xml(include_id=True))
    mail_template3 = MailTemplate.import_from_xml_tree(ET.fromstring(export), include_id=True)
    assert mail_template3.category_id is None


def test_mail_template_migration(pub):
    mail_template = MailTemplate(name='test template')
    mail_template.description = 'hello'
    assert mail_template.migrate() is True
    assert not mail_template.description
    assert mail_template.documentation == 'hello'


def test_mail_template_legacy_xml(pub):
    mail_template = MailTemplate(name='test template')
    mail_template.documentation = 'hello'
    export = ET.tostring(export_to_indented_xml(mail_template))
    export = export.replace(b'documentation>', b'description>')

    mail_template2 = MailTemplate.import_from_xml_tree(ET.fromstring(export))
    mail_template2.store()
    mail_template2.refresh_from_storage()
    assert mail_template2.documentation


def test_mail_template_documentation(pub, superuser):
    MailTemplate.wipe()
    mail_template = MailTemplate(name='foobar')
    mail_template.store()

    app = login(get_app(pub))

    resp = app.get(mail_template.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(mail_template.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    mail_template.refresh_from_storage()
    assert mail_template.documentation == '<p>doc</p>'
    resp = app.get(mail_template.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')


def test_mail_template_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/workflows/', status=403)

    MailTemplateCategory.wipe()
    WorkflowCategory.wipe()

    wf_cat = WorkflowCategory(name='wfcat')
    wf_cat.management_roles = [backoffice_role]
    wf_cat.store()
    app.get('/backoffice/workflows/', status=200)
    app.get('/backoffice/workflows/mail-templates/', status=403)

    cat = MailTemplateCategory(name='Foo')
    cat.store()

    app.get('/backoffice/workflows/mail-templates/', status=403)

    MailTemplate.wipe()
    mail_template = MailTemplate(name='mail template title')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.category_id = cat.id
    mail_template.store()

    mail_template2 = MailTemplate(name='mail2 template title')  # no category
    mail_template2.subject = 'test subject'
    mail_template2.body = 'test body'
    mail_template2.store()

    cat = MailTemplateCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/workflows/mail-templates/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'mail template title' not in resp.text  # mail template in that category
    assert 'mail2 template title' not in resp.text  # mail template in no category
    assert 'Bar' not in resp.text  # not yet any mail template in this category

    app.get(mail_template.get_admin_url(), status=403)
    app.get(mail_template2.get_admin_url(), status=403)

    resp = resp.click('New mail template')
    resp.form['name'] = 'mail template in category'
    assert len(resp.form['category_id'].options) == 1  # single option
    assert resp.form['category_id'].value == str(cat.id)  # the category managed by user
    resp = resp.form.submit('submit').follow()
    mt = MailTemplate.get_by_slug('mail-template-in-category')

    # check category select only let choose one
    resp = app.get(mt.get_admin_url())
    resp = resp.click(href='edit')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == str(cat.id)  # the category managed by user

    resp = app.get('/backoffice/workflows/mail-templates/')
    assert 'Bar' in resp.text  # now there's a data source in this category
    assert 'mail template in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    app.get('/backoffice/workflows/mail-templates/categories/', status=403)

    # no import into other category
    mail_template_xml = ET.tostring(mail_template.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('mail_template.wcs', mail_template_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    resp = app.get('/backoffice/studio/', status=200)
    resp.click('Workflows', index=0)
    resp.click('Mail templates', index=0)
    with pytest.raises(IndexError):
        resp.click('Comment templates', index=0)
    with pytest.raises(IndexError):
        resp.click('Data sources', index=0)
