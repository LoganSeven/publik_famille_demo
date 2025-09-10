import io
import zipfile

import pytest
from webtest import Upload

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.fields import ItemField, PageField, StringField
from wcs.formdef import FormDef
from wcs.i18n import TranslatableMessage
from wcs.mail_templates import MailTemplate
from wcs.qommon import ods
from wcs.qommon.http_request import HTTPRequest
from wcs.sql import Equal
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['en', 'fr']}
    pub.write_cfg()

    TranslatableMessage.do_table()  # update table with selected languages

    TranslatableMessage.wipe()
    Workflow.wipe()
    FormDef.wipe()
    BlockDef.wipe()
    Category.wipe()
    CardDef.wipe()
    MailTemplate.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_i18n_link_on_studio_page(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert '../i18n/' in resp.text
    pub.cfg['language']['multilinguism'] = False
    pub.write_cfg()
    resp = app.get('/backoffice/studio/')
    assert '../i18n/' not in resp.text
    app.get('/backoffice/i18n/', status=404)


def test_i18n_page(pub):
    create_superuser(pub)

    workflow = Workflow(name='workflow')
    st = workflow.add_status('First Status')
    sendmail = st.add_action('sendmail')
    sendmail.to = ['_submitter']
    sendmail.subject = 'Email Subject'
    sendmail.body = 'Email body'
    editable = st.add_action('editable')
    editable.label = 'Edit Button'
    workflow.add_global_action('Global Manual')
    action2 = workflow.add_global_action('Global No Trigger')
    action2.triggers = []
    workflow.store()

    workflow2 = Workflow(name='second workflow')
    workflow2.add_status('Other Status')

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        PageField(
            id='0',
            label='page field',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'blah'}, 'error_message': 'page error message'},
            ],
        ),
        StringField(id='1', label='text field'),
        StringField(
            id='2',
            label='text field',
            validation={'type': 'django', 'value': 'False', 'error_message': 'Custom Error'},
        ),
        ItemField(id='3', label='list field', items=['first', 'second', 'third']),
    ]
    formdef.workflow = workflow
    formdef.store()

    block = BlockDef(name='test')
    # check strings will be stripped
    block.fields = [StringField(id='1', label='text field ')]
    block.post_conditions = [
        {'condition': {'type': 'django', 'value': 'blah1'}, 'error_message': 'block post condition error'},
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'card test'
    carddef.store()

    category = Category(name='Category Name')
    category.store()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.store()

    app = login(get_app(pub))
    # first time goes to scanning
    resp = app.get('/backoffice/i18n/', status=302)
    resp = resp.follow()
    resp = resp.click('Go to multilinguism page')
    # second time, the page stays on
    resp = app.get('/backoffice/i18n/', status=200)

    # relaunch scan
    resp = resp.click('Rescan')
    resp = resp.follow()
    resp = resp.click('Go to multilinguism page')

    # check 'text field' only appears one
    assert TranslatableMessage.count([Equal('string', 'text field')]) == 1

    # check page post condition
    assert TranslatableMessage.count([Equal('string', 'page error message')]) == 1

    # check global action name appears only if there's a manual trigger
    assert TranslatableMessage.count([Equal('string', 'Global Manual')]) == 1
    assert TranslatableMessage.count([Equal('string', 'Global No Trigger')]) == 0

    # check edit button label
    assert TranslatableMessage.count([Equal('string', 'Edit Button')]) == 1

    # check custom validation message
    assert TranslatableMessage.count([Equal('string', 'Custom Error')]) == 1

    # check block post condition
    assert TranslatableMessage.count([Equal('string', 'block post condition error')]) == 1

    # check table
    assert resp.pyquery('tr').length == TranslatableMessage.count()

    # check filters
    assert resp.form['lang'].value == 'fr'
    assert [x[2] for x in resp.form['formdef'].options] == [
        'All forms and card models',
        'test title',
        'card test',
    ]
    resp.form['formdef'] = 'cards/1'
    resp = resp.form.submit()
    assert resp.pyquery('tr').length == 1
    assert {x.text for x in resp.pyquery('tr td:first-child')} == {'card test'}

    # check filtering on a formdef/carddef outputs related workflow strings
    resp.form['formdef'] = 'forms/1'
    resp = resp.form.submit()
    assert resp.pyquery('tr').length == 14
    assert 'test title' in {x.text for x in resp.pyquery('tr td:first-child')}
    assert 'Global Manual' in {x.text for x in resp.pyquery('tr td:first-child')}
    assert 'second workflow' not in {x.text for x in resp.pyquery('tr td:first-child')}

    resp.form['formdef'] = ''
    resp.form['q'] = 'Email'
    resp = resp.form.submit()
    assert resp.pyquery('tr').length == 2  # (email subject, email body)
    assert {x.text for x in resp.pyquery('tr td:first-child')} == {'Email body', 'Email Subject'}

    # translate a message
    msg = TranslatableMessage.select([Equal('string', 'Email body')])[0]
    resp = resp.click('edit', href='/%s/' % msg.id)
    resp = resp.form.submit('cancel').follow()
    resp = resp.click('edit', href='/%s/' % msg.id)
    assert resp.pyquery('.i18n-orig-string').text() == 'Email body'
    resp.form['translation'] = 'Texte du courriel'
    resp = resp.form.submit('submit').follow()
    msg = TranslatableMessage.get(msg.id)
    assert msg.string_fr == 'Texte du courriel'

    # go back
    resp = resp.click('edit', href='/%s/' % msg.id)
    assert resp.form['translation'].value == 'Texte du courriel'
    resp = resp.form.submit('submit').follow()

    # 404 pages
    resp = app.get('/backoffice/i18n/fr/%s/' % msg.id, status=200)
    resp = app.get('/backoffice/i18n/de/%s/' % msg.id, status=404)
    resp = app.get('/backoffice/i18n/fr/%s000/' % msg.id, status=404)


def test_i18n_export(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        StringField(id='1', label='text field'),
        ItemField(id='2', label='list field', items=['first', 'second', 'third']),
    ]
    formdef.store()

    # go and scan
    app = login(get_app(pub))
    resp = app.get('/backoffice/i18n/', status=302).follow()
    resp = resp.click('Go to multilinguism page')

    resp = resp.click('Export')
    resp = resp.form.submit('cancel').follow()
    resp = resp.click('Export')
    resp.form['format'] = 'ods'
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    assert resp.content_type == 'application/vnd.oasis.opendocument.spreadsheet'

    with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
        content = zipf.read('content.xml')
        assert b'>text field<' in content
        assert b'>list field<' in content

    resp = app.get('/backoffice/i18n/')
    resp = resp.click('Export')
    resp.form['format'] = 'xliff'
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    assert resp.content_type == 'text/xml'
    assert b'>text field<' in resp.body
    assert b'>list field<' in resp.body

    # check filtered strings
    resp = app.get('/backoffice/i18n/')
    resp.form['q'] = 'list'
    resp = resp.form.submit('submit')
    resp = resp.click('Export')
    resp.form['format'] = 'xliff'
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    assert resp.content_type == 'text/xml'
    assert b'>text field<' not in resp.body
    assert b'>list field<' in resp.body


def test_i18n_import(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        StringField(id='1', label='text field'),
        ItemField(id='2', label='list field', items=['first', 'second', 'third']),
    ]
    formdef.store()

    # go and scan
    app = login(get_app(pub))
    resp = app.get('/backoffice/i18n/', status=302).follow()
    resp = resp.click('Go to multilinguism page')

    resp = resp.click('Import')
    resp = resp.form.submit('cancel').follow()
    resp = resp.click('Import')
    resp.forms[0]['file'] = Upload(
        'test.xliff',
        b'''
<xliff:xliff xmlns:xliff="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="fr">
  <xliff:file id="f1">
    <xliff:file id="1">
      <xliff:segment>
        <xliff:source>text field</xliff:source>
        <xliff:target />
      </xliff:segment>
      <xliff:segment>
        <xliff:source>list field</xliff:source>
        <xliff:target>champ liste</xliff:target>
      </xliff:segment>
      <xliff:segment>
        <xliff:source>other text</xliff:source>
        <xliff:target>autre texte</xliff:target>
      </xliff:segment>
    </xliff:file>
  </xliff:file>
</xliff:xliff>
''',
        'text/xml',
    )
    resp = resp.form.submit('submit').follow()

    assert TranslatableMessage.count([Equal('string', 'text field')]) == 1
    assert TranslatableMessage.count([Equal('string', 'list field')]) == 1
    assert TranslatableMessage.count([Equal('string', 'other text')]) == 1
    assert TranslatableMessage.select([Equal('string', 'list field')])[0].string_fr == 'champ liste'
    assert TranslatableMessage.select([Equal('string', 'other text')])[0].string_fr == 'autre texte'

    TranslatableMessage.wipe()
    workbook = ods.Workbook(encoding='utf-8')
    ws = workbook.add_sheet('')
    ws.write(0, 0, 'list field')
    ws.write(0, 1, 'champ liste')
    ws.write(1, 0, 'other text')
    ws.write(1, 1, 'autre texte')
    output = io.BytesIO()
    workbook.save(output)

    resp = app.get('/backoffice/i18n/', status=302).follow()
    resp = resp.click('Go to multilinguism page')
    resp = resp.click('Import')

    resp.forms[0]['file'] = Upload(
        'test.ods', output.getvalue(), 'application/vnd.oasis.opendocument.spreadsheet'
    )
    resp = resp.form.submit('submit').follow()

    assert TranslatableMessage.count([Equal('string', 'text field')]) == 1
    assert TranslatableMessage.count([Equal('string', 'list field')]) == 1
    assert TranslatableMessage.count([Equal('string', 'other text')]) == 1
    assert TranslatableMessage.select([Equal('string', 'list field')])[0].string_fr == 'champ liste'
    assert TranslatableMessage.select([Equal('string', 'other text')])[0].string_fr == 'autre texte'

    # check query string is kept along
    resp = app.get('/backoffice/i18n/')
    resp.form['q'] = 'list'
    resp = resp.form.submit('submit')
    resp = resp.click('Import')
    resp.forms[0]['file'] = Upload(
        'test.ods', output.getvalue(), 'application/vnd.oasis.opendocument.spreadsheet'
    )
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Go to multilinguism')
    assert resp.request.url == 'http://example.net/backoffice/i18n/?q=list&formdef=&lang=fr'

    # invalid file
    resp = app.get('/backoffice/i18n/')
    resp = resp.click('Import')
    resp.forms[0]['file'] = Upload('test.txt', b'blah')
    resp = resp.form.submit('submit').follow()
    resp = app.get('/afterjobs/' + resp.pyquery('.afterjob').attr('id'))
    assert resp.json == {'status': 'failed', 'message': 'Unknown file format'}


def test_i18n_pagination(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = []
    for i in range(90):
        formdef.fields.append(StringField(id=str(i + 1), label='text field %s' % i))
    formdef.store()

    # go and scan
    app = login(get_app(pub))
    resp = app.get('/backoffice/i18n/', status=302).follow()
    resp = resp.click('Go to multilinguism page')

    # check page limit
    assert resp.pyquery('#page-links a').text() == '1 2 3 4 5  10 50 100'
    resp = resp.click('50')
    assert resp.pyquery('#page-links a').text() == '1 2  10 20 100'
    resp = resp.click('20')
    resp = resp.click('3')
    assert 'offset=40' in resp.request.url


def test_i18n_mark_as_non_translatabe(pub):
    create_superuser(pub)
    workflow = Workflow(name='workflow')
    workflow.add_status('First Status')
    workflow.add_status('Second Status')
    workflow.store()

    app = login(get_app(pub))
    # first time goes to scanning
    resp = app.get('/backoffice/i18n/', status=302)
    resp = resp.follow()
    resp = resp.click('Go to multilinguism page')
    # second time, the page stays on
    resp = app.get('/backoffice/i18n/', status=200)

    assert TranslatableMessage.count() == 2  # First Status / Second Status
    assert resp.pyquery('tr').length == 2

    # check form filter
    assert resp.form['lang'].value == 'fr'
    resp.form['q'] = 'First'
    resp = resp.form.submit()
    assert resp.pyquery('tr').length == 1

    # mark a message as non translatable
    resp = resp.click('edit', index=0)
    resp.form['non_translatable'].checked = True
    resp = resp.form.submit('submit').follow()
    msg = TranslatableMessage.select([Equal('string', 'First Status')])[0]
    assert msg.translatable is False

    resp = app.get('/backoffice/i18n/', status=200)
    assert resp.pyquery('tr').length == 1
    assert resp.pyquery('tr td:first-child').text() == 'Second Status'
    resp.form['non_translatable'].checked = True
    resp = resp.form.submit('submit')
    assert resp.pyquery('tr').length == 1
    assert resp.pyquery('tr td:first-child').text() == 'First Status'


def test_i18n_but_no_language(pub):
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['en']}
    pub.write_cfg()
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/i18n/', status=200)
    assert 'No languages selected.' in resp.text
