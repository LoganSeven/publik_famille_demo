import io
import json
import os
import urllib.parse
import zipfile

try:
    import lasso  # pylint: disable=unused-import
except ImportError:
    lasso = None

import pytest
import responses
from quixote.http_request import Upload as QuixoteUpload
from webtest import Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.audit import Audit
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon import misc
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.sql import ApiAccess
from wcs.wf.export_to_model import ExportToModel
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_settings(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/settings/')

    app.get('/backoffice/settings/debug_options')
    app.get('/backoffice/settings/language')
    app.get('/backoffice/settings/import')
    app.get('/backoffice/settings/export')
    app.get('/backoffice/settings/identification')
    app.get('/backoffice/settings/sitename')
    app.get('/backoffice/settings/sms')
    app.get('/backoffice/settings/admin-permissions')


def test_settings_disabled_screens(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/')
    assert 'Identification' in resp.text

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set(
        'options', 'settings-disabled-screens', 'identification, import-export, geolocation, smtp'
    )
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/settings/')
    assert 'Identification' not in resp.text
    app.get('/backoffice/settings/identification/', status=404)
    assert 'Import / Export' not in resp.text
    app.get('/backoffice/settings/import', status=404)
    app.get('/backoffice/settings/export', status=404)
    app.get('/backoffice/settings/geolocation', status=404)

    resp = app.get('/backoffice/settings/emails/options')
    assert not resp.pyquery('#form_smtp_server')
    resp.form['from'] = 'test@localhost'
    resp = resp.form.submit('submit')

    pub.site_options.set('options', 'settings-disabled-screens', '')
    pub.site_options.set('options', 'settings-hidden-screens', 'import-export')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert 'Import / Export' not in resp.text
    app.get('/backoffice/settings/import', status=200)


def test_settings_export_import(pub):
    def wipe():
        FormDef.wipe()
        CardDef.wipe()
        Workflow.wipe()
        pub.role_class.wipe()
        Category.wipe()
        CardDefCategory.wipe()
        WorkflowCategory.wipe()
        NamedDataSource.wipe()
        NamedWsCall.wipe()
        ApiAccess.wipe()
        BlockCategory.wipe()
        MailTemplateCategory.wipe()
        CommentTemplateCategory.wipe()
        DataSourceCategory.wipe()
        MailTemplate.wipe()
        CommentTemplate.wipe()
        BlockDef.wipe()

    wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('cancel')
    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('submit')
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['job'][0]
    resp = resp.follow()
    assert 'completed' in resp.text
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        filelist = zipf.namelist()
    assert len(filelist) == 0

    # check afterjob ajax call
    status_resp = app.get('/afterjobs/' + job_id)
    assert status_resp.json == {'status': 'completed', 'message': 'completed'}

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    carddef = CardDef()
    carddef.name = 'bar'
    carddef.store()
    form_cat = Category(name='baz')
    form_cat.store()
    form_cat2 = Category(name='baz2')
    form_cat2.store()
    card_cat = CardDefCategory(name='foobar')
    card_cat.store()
    workflow_cat = WorkflowCategory(name='foobaz')
    workflow_cat.store()
    block_cat = BlockCategory(name='category for blocks')
    block_cat.store()
    mail_template_cat = MailTemplateCategory(name='category for mail templates')
    mail_template_cat.store()
    comment_template_cat = CommentTemplateCategory(name='category for mail templates')
    comment_template_cat.store()
    data_source_cat = DataSourceCategory(name='category for data sources')
    data_source_cat.store()
    MailTemplate(name='Mail templates').store()
    CommentTemplate(name='Comment templates').store()
    pub.role_class(name='qux').store()
    NamedDataSource(name='quux').store()
    BlockDef(name='blockdef').store()
    ds = NamedDataSource(name='agenda')
    ds.external = 'agenda'
    ds.store()
    NamedWsCall(name='corge').store()

    wf = Workflow(name='bar')
    st1 = wf.add_status('Status1', 'st1')
    export_to = ExportToModel()
    export_to.label = 'test'
    upload = QuixoteUpload('/foo/bar', content_type='application/vnd.oasis.opendocument.text')
    file_content = (
        b'PK\x03\x04\n\x00\x00\x00\x00\x00\x8edHZ\xeff\xaf\xd4\x05\x00\x00\x00'
        b'\x05\x00\x00\x00\x0b\x00\x1c\x00content.xmlUT\t\x00\x03\xbcA\xa7g\xb7A'
        b'\xa7gux\x0b\x00\x01\x04\xe8\x03\x00\x00\x04\xe8\x03\x00\x00<t/>\nPK'
        b'\x01\x02\x1e\x03\n\x00\x00\x00\x00\x00\x8edHZ\xeff\xaf\xd4\x05\x00\x00'
        b'\x00\x05\x00\x00\x00\x0b\x00\x18\x00\x00\x00\x00\x00\x01\x00\x00\x00\xb4\x81'
        b'\x00\x00\x00\x00content.xmlUT\x05\x00\x03\xbcA\xa7gux\x0b\x00\x01\x04\xe8\x03'
        b'\x00\x00\x04\xe8\x03\x00\x00PK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x00Q\x00'
        b'\x00\x00J\x00\x00\x00\x00\x00'
    )
    upload.fp = io.BytesIO()
    upload.fp.write(file_content)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile('models', 'export_to_model-1.upload', upload)
    st1.items.append(export_to)
    export_to.parent = st1
    wf.store()

    api_access = ApiAccess()
    api_access.name = 'Jhon'
    api_access.api_identifier = 'jhon'
    api_access.api_key = '1234'
    api_access.store()

    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        filelist = zipf.namelist()
    assert 'formdefs/1' not in filelist
    assert 'formdefs_xml/1' in filelist
    assert 'carddefs/1' not in filelist
    assert 'carddefs_xml/1' in filelist
    assert 'workflows/1' not in filelist
    assert 'workflows_xml/1' in filelist
    assert 'models/export_to_model-1.upload' not in filelist
    assert 'roles/1' not in filelist
    assert 'roles_xml/1' in filelist
    assert f'categories/{form_cat.id}' in filelist
    assert f'carddef_categories/{card_cat.id}' in filelist
    assert f'workflow_categories/{workflow_cat.id}' in filelist
    assert f'block_categories/{block_cat.id}' in filelist
    assert f'mail_template_categories/{mail_template_cat.id}' in filelist
    assert f'comment_template_categories/{comment_template_cat.id}' in filelist
    assert f'data_source_categories/{data_source_cat.id}' in filelist
    assert 'datasources/1' in filelist
    assert 'datasources/2' not in filelist  # agenda datasource, not exported
    assert 'mail-templates/1' in filelist
    assert 'comment-templates/1' in filelist
    assert 'wscalls/1' in filelist
    assert 'apiaccess/1' in filelist
    for filename in filelist:
        assert '.indexes' not in filename

    wipe()
    assert FormDef.count() == 0

    resp = app.get('/backoffice/settings/import')
    assert 'This site has existing' not in resp.text
    resp = resp.form.submit('cancel')

    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', b'invalid content')
    resp = resp.form.submit('submit').follow()
    assert 'Error: Not a valid export file' in resp.text

    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('.afterjob').text() == 'completed'
    resp = resp.click('Import report')
    assert 'Imported successfully' in resp.text
    assert '1 form</li>' in resp.text
    assert '1 card</li>' in resp.text
    assert '1 block of fields</li>' in resp.text
    assert '1 workflow</li>' in resp.text
    assert '1 role</li>' in resp.text
    assert '2 categories</li>' in resp.text
    assert '1 card category</li>' in resp.text
    assert '1 workflow category</li>' in resp.text
    assert '1 block category</li>' in resp.text
    assert '1 mail template category</li>' in resp.text
    assert '1 comment template category</li>' in resp.text
    assert '1 data source category</li>' in resp.text
    assert '1 data source</li>' in resp.text
    assert '1 mail template</li>' in resp.text
    assert '1 comment template</li>' in resp.text
    assert '1 webservice call</li>' in resp.text
    assert '1 API access</li>' in resp.text
    assert FormDef.count() == 1
    assert FormDef.select()[0].url_name == 'foo'
    assert CardDef.count() == 1
    assert CardDef.select()[0].url_name == 'bar'
    assert BlockDef.count() == 1
    assert Workflow.count() == 1
    assert pub.role_class.count() == 1
    assert Category.count() == 2
    assert CardDefCategory.count() == 1
    assert WorkflowCategory.count() == 1
    assert BlockCategory.count() == 1
    assert MailTemplateCategory.count() == 1
    assert CommentTemplateCategory.count() == 1
    assert DataSourceCategory.count() == 1
    assert NamedDataSource.count() == 1
    assert MailTemplate.count() == 1
    assert CommentTemplate.count() == 1
    assert NamedWsCall.count() == 1
    assert ApiAccess.count() == 1

    # check roles are found by name
    wipe()
    role = pub.role_class(name='qux')
    role.store()

    workflow = Workflow(name='Workflow One')
    st1 = workflow.add_status(name='st1')
    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [role.id]
    commentable.label = 'foobar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow_id = workflow.id
    formdef.roles = [role.id]
    formdef.backoffice_submission_roles = [role.id]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [fields.StringField(id='1', data_source={'type': 'carddef:unknown'})]
    formdef.store()

    resp = app.get('/backoffice/settings/export')
    resp.form['items$elementformdefs'] = True
    resp.form['items$elementworkflows'] = True
    resp.form['items$elementroles'] = False
    resp.form['items$elementcategories'] = False
    resp.form['items$elementdatasources'] = False
    resp.form['items$elementwscalls'] = False
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        filelist = zipf.namelist()
    assert 'formdefs_xml/%s' % formdef.id in filelist
    assert 'workflows_xml/%s' % workflow.id in filelist
    assert 'roles_xml/%s' % role.id not in filelist

    FormDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()

    # create role beforehand, it should be matched by name
    role = pub.role_class(name='qux')
    role.id = '012345'
    role.store()

    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp = resp.form.submit('submit')
    assert FormDef.select()[0].roles == ['012345']
    assert FormDef.select()[0].backoffice_submission_roles == ['012345']
    assert FormDef.select()[0].workflow_roles == {'_receiver': '012345'}
    assert len(FormDef.select()[0].fields) == 1
    assert Workflow.select()[0].possible_status[0].items[0].by == ['012345']

    # do not export roles when managed by idp
    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()
    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        filelist = zipf.namelist()
    assert len([x for x in filelist if 'roles_xml/' in x]) == 0

    # check a warning is displayed if there's some content already
    resp = app.get('/backoffice/settings/import')
    assert 'This site has existing' in resp.text
    # check a confirmation is required
    assert resp.form['submit'].attrs['data-ask-for-confirmation']
    resp = resp.form.submit('submit')
    # check a checkbox is required
    assert resp.pyquery('[data-widget-name="confirm"].widget-with-error')

    # check an error is displayed if such an import is then used and roles are
    # missing.
    FormDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()
    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp = resp.form.submit('submit').follow()
    assert 'Unknown referenced objects [Unknown roles: qux]' in resp

    # unknown field block
    Workflow.wipe()
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [fields.BlockField(id='1', block_slug='unknown')]
    formdef.store()
    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp.form['confirm'].checked = True
    resp = resp.form.submit('submit').follow()
    assert 'Unknown referenced objects [Unknown blocks of fields: unknown]' in resp

    # Unknown reference in blockdef
    BlockDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    blockdef = BlockDef()
    blockdef.name = 'foo'
    blockdef.fields = [
        fields.StringField(id='1', data_source={'type': 'foobar'}),
    ]
    blockdef.store()
    resp = app.get('/backoffice/settings/export')
    resp = resp.form.submit('submit').follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp = resp.form.submit('submit').follow()
    assert 'Unknown referenced objects [Unknown datasources: foobar]' in resp
    BlockDef.wipe()

    # Categories with duplicated id
    zip_content = io.BytesIO()
    with zipfile.ZipFile(zip_content, 'w') as z:
        z.writestr(
            'categories/1',
            b'''<category id="1">
                  <name>form cat1</name>
                  <url_name>form-cat1</url_name>
                  <position>1</position>
               </category>''',
        )
        z.writestr(
            'carddef_categories/1',
            b'''<carddef_category id="1">
                  <name>card cat1</name>
                  <url_name>card-cat1</url_name>
                  <position>1</position>
               </carddef_category>''',
        )

    resp = app.get('/backoffice/settings/import')
    resp.form['file'] = Upload('export.wcs', zip_content.getvalue())
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('.afterjob').text() == 'Error: Exported site needs to be migrated to SQL categories.'


def test_settings_export_import_admin_permissions(pub):
    create_superuser(pub)
    role = pub.role_class(name='qux')
    role.store()

    pub.cfg['admin-permissions'] = {'forms': [role.id]}
    pub.write_cfg()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/export')
    for key in resp.form.fields.keys():
        if key.startswith('items$'):
            resp.form[key].checked = False
    resp.form['items$elementsettings'].checked = True
    resp = resp.form.submit('submit')
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    resp = resp.follow()
    resp = resp.click('Download Export')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        assert zipf.namelist() == ['config.json']
        exported_cfg = json.loads(zipf.read('config.json'))
    assert exported_cfg['admin-permissions-export']['forms'] == [
        {'id': role.id, 'uuid': None, 'slug': role.slug, 'name': role.name}
    ]

    pub.cfg['admin-permissions'] = {}
    pub.write_cfg()
    pub.role_class.wipe()
    role = pub.role_class(name='foo')
    role.store()
    role = pub.role_class(name='qux')
    role.store()

    # check admin permissions has a reference to new role
    pub.import_zip(io.BytesIO(resp.body))
    assert pub.cfg['admin-permissions']['forms'] == [role.id]
    assert 'admin-permissions-export' not in pub.cfg


def test_settings_user(pub):
    user = create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/users').follow().follow()

    # add a field
    resp.forms[2]['label'] = 'foobar'
    resp = resp.forms[2].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert b'foobar' in pub.cfg['users']['formdef']
    assert 'foobar' in resp.text
    formdef = UserFieldsFormDef()
    field = formdef.fields[0]
    resp = app.get('/backoffice/settings/users/fields/%s/' % field.id)
    resp.form['varname'] = 'foobar'
    resp = resp.form.submit('submit').follow()

    # give the user a value for this new attribute
    user.form_data = {field.id: 'plop'}
    user.store()

    # set field as email
    resp.forms['mapping']['field_email'] = field.id
    resp = resp.forms['mapping'].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert pub.cfg['users']['field_email'] == field.id

    # and unset it
    resp.forms['mapping']['field_email'] = ''
    resp = resp.forms['mapping'].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert pub.cfg['users']['field_email'] is None

    # set field as phone
    resp.forms['mapping']['field_phone'] = field.id
    resp = resp.forms['mapping'].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert pub.cfg['users']['field_phone'] == field.id

    # and unset it
    resp.forms['mapping']['field_phone'] = ''
    resp = resp.forms['mapping'].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert pub.cfg['users']['field_phone'] is None

    # add a comment field
    resp.forms[2]['label'] = 'barfoo'
    resp.forms[2]['type'] = 'comment'
    resp = resp.forms[2].submit()
    assert resp.location == 'http://example.net/backoffice/settings/users/fields/'
    resp = resp.follow()
    assert b'barfoo' in pub.cfg['users']['formdef']
    assert 'barfoo' in resp.text

    # check fields are present in edit form
    resp = app.get('/backoffice/users/%s/edit' % user.id)
    assert 'barfoo' in resp.text
    assert 'f%s' % field.id in resp.form.fields
    assert 'email' in resp.form.fields

    # check the email field is not displayed if it's overridden by a custom
    # field.
    pub.cfg['users']['field_email'] = field.id
    pub.write_cfg()
    resp = app.get('/backoffice/users/%s/edit' % user.id)
    assert 'f%s' % field.id in resp.form.fields
    assert 'email' not in resp.form.fields

    # check migration code for fullname template
    pub.cfg['users'].pop('fullname_template', None)
    pub.cfg['users']['field_name'] = [field.id]
    pub.write_cfg()
    resp = app.get('/backoffice/settings/users/fields/')
    assert resp.forms['templates']['fullname_template'].value == '{{ user_var_foobar|default:"" }}'
    resp = resp.forms['templates'].submit().follow()
    assert pub.cfg['users']['fullname_template'] == '{{ user_var_foobar|default:"" }}'

    # change fullname template, check users are updated
    resp.forms['templates']['fullname_template'].value = 'x{{ user_var_foobar|default:"" }}y'
    resp = resp.forms['templates'].submit().follow()
    user.refresh_from_storage()
    assert user.name == 'xplopy'

    # set a sidebar template
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/users').follow().follow()
    resp.forms['templates']['sidebar_template'] = 'hello {{ form_user_display_name }}'
    resp = resp.forms['templates'].submit().follow()
    assert pub.cfg['users']['sidebar_template'] == 'hello {{ form_user_display_name }}'
    resp.forms['templates']['sidebar_template'] = '{% if True %}'
    resp = resp.forms['templates'].submit().follow()
    assert pub.cfg['users']['sidebar_template'] == 'hello {{ form_user_display_name }}'
    assert 'syntax error in Django template' in resp

    # set a search result template
    pub.reload_cfg()
    pub.cfg['users'].pop('search_result_template', None)
    resp = app.get('/backoffice/settings/users/fields/')
    assert resp.forms['templates']['search_result_template'].value.replace('\n', '') == (
        '{{ user_email|default:"" }}'
        '{% if user_var_phone %} 📞 {{ user_var_phone }}{% endif %}'
        '{% if user_var_mobile %} 📱 {{ user_var_mobile }}{% endif %}'
        '{% if user_var_address or user_var_zipcode or user_var_city %} 📨{% endif %}'
        '{% if user_var_address %} {{ user_var_address }}{% endif %}'
        '{% if user_var_zipcode %} {{ user_var_zipcode }}{% endif %}'
        '{% if user_var_city %} {{ user_var_city }}{% endif %}'
    )
    resp.forms['templates']['search_result_template'] = '{{ user_email|default:"" }} Foo Bar'
    resp = resp.forms['templates'].submit().follow()
    assert pub.cfg['users']['search_result_template'] == '{{ user_email|default:"" }} Foo Bar'

    # disable users screen
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'settings-disabled-screens', 'users')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/backoffice/settings/')
    resp = resp.click('Users', href='user-templates')
    resp.forms['templates']['sidebar_template'] = '{% if True %}'
    resp = resp.forms['templates'].submit()
    assert 'syntax error in Django template' in resp
    resp.forms['templates']['fullname_template'] = 'T{{ user_email }}'
    resp.forms['templates']['sidebar_template'] = 'hello {{ form_user_display_name }}'
    resp.forms['templates']['search_result_template'] = '{{ form_user_display_name }}'
    resp = resp.forms['templates'].submit()
    assert pub.cfg['users']['fullname_template'] == 'T{{ user_email }}'
    assert pub.cfg['users']['sidebar_template'] == 'hello {{ form_user_display_name }}'
    assert pub.cfg['users']['search_result_template'] == '{{ form_user_display_name }}'
    # check user has been updated for new fullname template
    user.refresh_from_storage()
    assert user.name == 'Tplop'

    # restore config
    pub.cfg['users']['field_email'] = None
    pub.cfg['users']['fullname_template'] = None
    pub.write_cfg()

    # check audit log
    assert Audit.select(order_by='id')[-1].action == 'settings'
    assert Audit.select(order_by='id')[-1].extra_data == {'cfg_key': 'users'}


def test_settings_emails(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    pub.cfg['debug'] = {'mail_redirection': 'foo@example.net'}
    pub.write_cfg()
    resp = app.get('/backoffice/settings/emails/')
    resp = resp.click('General Options')
    assert 'Warning: all emails are sent to &lt;foo@example.net&gt;' in resp.text
    resp.form['from'] = 'test@localhost'
    resp = resp.form.submit('submit')
    pub.reload_cfg()
    assert pub.cfg['emails']['from'] == 'test@localhost'
    assert pub.cfg['emails']['well_known_domains']
    assert pub.cfg['emails']['valid_known_domains']

    pub.cfg['debug'] = {}
    pub.write_cfg()
    resp = app.get('/backoffice/settings/emails/')
    resp = resp.click('General Options')
    assert 'Warning: all emails are sent to &lt;foo@example.net&gt;' not in resp.text

    resp = app.get('/backoffice/settings/emails/')
    resp = resp.click('Approval of new account')
    resp.forms[0]['email-new-account-approved_subject'] = 'bla'
    resp.forms[0]['email-new-account-approved'] = 'bla bla bla'
    resp = resp.forms[0].submit()
    assert pub.cfg['emails']['email-new-account-approved_subject'] == 'bla'
    assert pub.cfg['emails']['email-new-account-approved'] == 'bla bla bla'

    # reset to default value
    resp = app.get('/backoffice/settings/emails/')
    resp = resp.click('Approval of new account')
    resp.forms[0]['email-new-account-approved_subject'] = 'Your account has been approved'
    resp = resp.forms[0].submit()
    assert pub.cfg['emails']['email-new-account-approved_subject'] is None

    # disable password authentication method
    pub.cfg['identification'] = {'methods': []}
    pub.write_cfg()
    resp = app.get('/backoffice/settings/emails/')
    assert 'Approval of new account' not in resp.text

    # check audit log
    assert Audit.select(order_by='id')[-1].action == 'settings'
    assert Audit.select(order_by='id')[-1].extra_data == {
        'cfg_key': 'emails',
        'cfg_email_key': 'new-account-approved',
    }


def test_settings_texts(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/texts/')
    resp = resp.click('Text on top of the login page')
    resp.forms[0]['text-top-of-login'] = 'Hello world'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/settings/texts/'
    assert pub.cfg['texts']['text-top-of-login'] == 'Hello world'

    resp = app.get('/backoffice/settings/texts/')
    resp = resp.click('Text on top of the login page')
    resp = resp.forms[0].submit('restore-default')
    assert resp.location == 'http://example.net/backoffice/settings/texts/'
    assert pub.cfg['texts']['text-top-of-login'] is None

    # disable password authentication method
    pub.cfg['identification'] = {'methods': []}
    pub.write_cfg()
    resp = app.get('/backoffice/settings/texts/')
    assert 'Text on top of the login page' not in resp.text

    # captcha text
    assert 'CAPTCHA' in resp.text
    pub.site_options.set('options', 'formdef-captcha-option', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/settings/texts/')
    assert 'CAPTCHA' not in resp.text

    # welcome texts
    assert 'welcome' in resp.text
    pub.cfg['misc']['homepage-redirect-url'] = 'http://www.example.com/'
    pub.write_cfg()
    resp = app.get('/backoffice/settings/texts/')
    assert 'welcome' not in resp.text

    # check audit log
    assert Audit.select(order_by='id')[-1].action == 'settings'
    assert Audit.select(order_by='id')[-1].extra_data == {'cfg_key': 'texts', 'cfg_text_key': 'top-of-login'}


@pytest.mark.skipif('lasso is None')
def test_settings_auth(pub):
    pub.user_class.wipe()  # makes sure there are no users
    pub.cfg['identification'] = {}
    pub.write_cfg()
    app = get_app(pub)

    resp = app.get('/backoffice/settings/')
    assert 'identification/password/' not in resp.text
    assert 'identification/idp/' not in resp.text

    resp = resp.click('Identification')
    assert resp.forms[0]['methods$elementidp'].checked is False
    assert resp.forms[0]['methods$elementpassword'].checked is False
    resp.forms[0]['methods$elementidp'].checked = True
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert 'identification/idp/' in resp.text
    assert pub.cfg['identification']['methods'] == ['idp']

    resp = resp.click('Identification')
    assert resp.forms[0]['methods$elementidp'].checked is True
    assert resp.forms[0]['methods$elementpassword'].checked is False
    resp.forms[0]['methods$elementidp'].checked = False
    resp.forms[0]['methods$elementpassword'].checked = True
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert 'identification/password/' in resp.text
    assert pub.cfg['identification']['methods'] == ['password']

    # check audit log
    assert Audit.select(order_by='id')[-1].action == 'settings'
    assert Audit.select(order_by='id')[-1].extra_data == {'cfg_key': 'identification'}


@pytest.mark.skipif('lasso is None')
def test_settings_idp(pub):
    # create admin session
    create_superuser(pub)
    app = login(get_app(pub))

    pub.cfg['identification'] = {'methods': ['idp']}
    pub.write_cfg()
    app.get('/saml/metadata', status=404)
    resp = app.get('/backoffice/settings/')
    resp = resp.click(href='identification/idp/')
    resp = resp.click('Service Provider')
    resp = resp.form.submit('generate_rsa').follow()
    resp = resp.form.submit('submit')
    resp = resp.follow()

    resp2 = resp.click('Identities')
    resp2 = resp2.form.submit('cancel').follow()
    resp2 = resp.click('Identities')
    resp2 = resp2.form.submit('submit')

    resp_metadata = app.get('/saml/metadata', status=200)
    assert resp_metadata.text.startswith('<?xml')
    resp2 = resp.click('Identity Providers')
    resp2 = resp2.click('New')
    idp_metadata_filename = os.path.join(os.path.dirname(__file__), '..', 'idp_metadata.xml')
    with open(idp_metadata_filename, 'rb') as fd:
        resp2.form['metadata'] = Upload('idp_metadata.xml', fd.read())
    resp2 = resp2.form.submit('submit')

    resp = resp.click('Identity Providers')
    assert 'http://authentic.example.net/' in resp.text
    resp2 = resp.click(href='http-authentic.example.net-idp-saml2-metadata/', index=0)
    assert 'ns0:EntityDescriptor' in resp2.text
    resp = resp.click(href='http-authentic.example.net-idp-saml2-metadata/edit')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    # test that login initiates a SSO
    login_resp = app.get('/login/', status=302)
    assert login_resp.location.startswith('http://authentic.example.net/idp/saml2/sso?SAMLRequest')

    resp = resp.click(href='/backoffice/settings/identification/idp/idp/', index=0)  # breadcrumb
    resp = resp.click(href='http-authentic.example.net-idp-saml2-metadata/delete')
    resp = resp.forms[0].submit()  # confirm delete
    assert len(pub.cfg['idp']) == 0

    with responses.RequestsMock() as rsps:
        idp_metadata_filename = os.path.join(os.path.dirname(__file__), '..', 'idp_metadata.xml')
        with open(idp_metadata_filename, 'rb') as body:
            rsps.get('http://authentic.example.net/idp/saml2/metadata', body=body.read())
        resp = app.get('/backoffice/settings/identification/idp/idp/')
        resp = resp.click('Create new from remote URL')
        resp.form['metadata_url'] = 'http://authentic.example.net/idp/saml2/metadata'
        resp = resp.form.submit('submit')
        resp = resp.follow()
        assert 'http://authentic.example.net/idp/saml2/metadata' in resp.text
        assert len(rsps.calls) == 1
        resp = resp.click(resp.pyquery('.biglistitem--content a').text())
        resp = resp.click('Update from remote URL')
        assert len(rsps.calls) == 2


def test_settings_auth_password(pub):
    pub.role_class.wipe()

    pub.user_class.wipe()  # makes sure there are no users
    pub.cfg['identification'] = {'methods': ['password']}
    assert pub.cfg['identification']['methods'] == ['password']
    pub.write_cfg()
    app = get_app(pub)

    resp = app.get('/backoffice/settings/identification/password/')
    resp = resp.click('Identities')
    resp = resp.forms[0].submit()

    resp = app.get('/backoffice/settings/identification/password/')
    resp = resp.click('Passwords')
    resp = resp.forms[0].submit()


def test_settings_filetypes(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/filetypes/')
    assert 'There are no file type defined at the moment.' in resp.text

    resp = resp.click('New file type')
    resp.forms[0]['label'] = 'Text files'
    resp.forms[0]['mimetypes'] = '.odt'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/filetypes/'
    resp = resp.follow()
    assert pub.cfg['filetypes'][1]['label'] == 'Text files'

    resp = resp.click('Text files')
    assert resp.forms[0]['mimetypes'].value == 'application/vnd.oasis.opendocument.text'
    resp.forms[0]['mimetypes'] = 'application/vnd.oasis.opendocument.text, .doc, .docx, .pdf'
    resp = resp.forms[0].submit('submit')
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    resp = resp.follow()
    assert 'completed' in resp.text
    resp = resp.click('Back to settings')
    assert 'application/msword (.' in resp.text
    assert 'application/pdf' in pub.cfg['filetypes'][1]['mimetypes']

    resp = resp.click('New file type')
    resp.forms[0]['label'] = 'HTML files'
    resp.forms[0]['mimetypes'] = '.html'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    resp = resp.click('HTML files')  # go to form
    resp = resp.forms[0].submit('cancel')  # and cancel
    assert resp.location == 'http://example.net/backoffice/settings/filetypes/'
    resp = resp.follow()
    assert 'HTML files' in resp.text

    resp = resp.click('HTML files')  # go to form
    resp = resp.click('Delete')
    resp = resp.forms[0].submit('cancel').follow()  # and cancel
    resp = resp.click('Delete')
    resp = resp.forms[0].submit('delete')  # and delete
    assert resp.location == 'http://example.net/backoffice/settings/filetypes/'
    resp = resp.follow()
    assert 'HTML files' not in resp.text

    resp = app.get('/backoffice/settings/filetypes/')
    resp = resp.click('New file type')
    resp = resp.forms[0].submit('submit')
    assert 'This field is required.' in resp

    # check default
    assert misc.get_document_type_value_options(None)[0][1] == '---'
    resp = app.get('/backoffice/settings/filetypes/')
    resp = resp.click('Text files')
    resp.forms[0]['is_default'].checked = True
    resp = resp.forms[0].submit('submit').follow()
    assert misc.get_document_type_value_options(None)[0][1] == 'Default value (Text files)'

    resp = resp.click('New file type')
    resp.forms[0]['label'] = 'HTML files'
    resp.forms[0]['mimetypes'] = '.html'
    resp.forms[0]['is_default'].checked = True
    resp = resp.forms[0].submit('submit').follow()
    assert misc.get_document_type_value_options(None)[0][1] == 'Default value (HTML files)'


def test_settings_filetypes_update(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    pub.cfg['filetypes'] = {
        1: {'mimetypes': ['application/pdf', 'application/msword'], 'label': 'Text files'}
    }
    pub.write_cfg()
    resp = app.get('/backoffice/settings/filetypes/')
    assert 'Text files' in resp.text

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.FileField(
            id='1',
            label='1st field',
            document_type={
                'id': 1,
                'mimetypes': ['application/pdf', 'application/msword'],
                'label': 'Text files',
            },
        )
    ]
    formdef.store()
    assert FormDef.get(formdef.id).fields[0].document_type == {
        'id': 1,
        'mimetypes': ['application/pdf', 'application/msword'],
        'label': 'Text files',
    }

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = formdef.fields
    carddef.store()

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'block title'
    blockdef.fields = formdef.fields
    blockdef.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.document_type = formdef.fields[0].document_type
    attach.by = ['_submitter']
    display_form = st1.add_action('form')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = formdef.fields
    wf.store()

    resp = resp.click('Text files')
    resp.forms[0]['mimetypes'] = 'application/vnd.oasis.opendocument.text, .doc, .docx, .pdf'
    resp = resp.forms[0].submit('submit')
    assert 'application/pdf' in pub.cfg['filetypes'][1]['mimetypes']
    assert FormDef.get(formdef.id).fields[0].document_type == {
        'id': 1,
        'mimetypes': [
            'application/vnd.oasis.opendocument.text',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/pdf',
        ],
        'label': 'Text files',
    }
    assert CardDef.get(carddef.id).fields[0].document_type == {
        'id': 1,
        'mimetypes': [
            'application/vnd.oasis.opendocument.text',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/pdf',
        ],
        'label': 'Text files',
    }
    assert BlockDef.get(blockdef.id).fields[0].document_type == {
        'id': 1,
        'mimetypes': [
            'application/vnd.oasis.opendocument.text',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/pdf',
        ],
        'label': 'Text files',
    }
    assert Workflow.get(wf.id).possible_status[0].items[0].document_type == {
        'id': 1,
        'mimetypes': [
            'application/vnd.oasis.opendocument.text',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/pdf',
        ],
        'label': 'Text files',
    }
    assert Workflow.get(wf.id).possible_status[0].items[1].formdef.fields[0].document_type == {
        'id': 1,
        'mimetypes': [
            'application/vnd.oasis.opendocument.text',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/pdf',
        ],
        'label': 'Text files',
    }

    resp = app.get('/backoffice/settings/filetypes/')
    resp = resp.click('Text files')
    resp.forms[0]['mimetypes'] = ''
    resp = resp.forms[0].submit('submit')
    assert 'This field is required.' in resp


def test_settings_geolocation(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    assert pub.get_default_zoom_level() == '13'

    resp = app.get('/backoffice/settings/')
    resp = resp.click('Geolocation')
    resp.form['default-position$latlng'].value = '1.234;-1.234'
    resp = resp.form.submit('cancel').follow()
    resp = resp.click('Geolocation')
    assert 'value="1.234;-1.234' not in resp.text
    resp.form['default-position$latlng'].value = '1.234;-1.234'
    resp = resp.form.submit().follow()
    resp = resp.click('Geolocation')
    assert 'value="1.234;-1.234' in resp.text
    pub.reload_cfg()
    assert pub.cfg['misc']['default-position'] == {'lat': 1.234, 'lon': -1.234}

    assert pub.cfg['misc']['default-zoom-level'] == '13'
    resp = resp.click('Geolocation')
    resp.form['default-zoom-level'] = '16'
    resp = resp.form.submit().follow()
    assert pub.cfg['misc']['default-zoom-level'] == '16'

    resp = resp.click('Geolocation')
    assert resp.form['geocoding-services-base-url'].value == ''
    resp = resp.form.submit().follow()

    assert pub.get_geocoding_service_url() == 'https://nominatim.openstreetmap.org/search'
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'nominatim_url', 'https://nominatim.example.org')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert pub.get_geocoding_service_url() == 'https://nominatim.example.org/search'

    resp = resp.click('Geolocation')
    assert resp.form['geocoding-services-base-url'].value == 'https://nominatim.example.org'
    resp.form['geocoding-services-base-url'] = 'https://nominatim.org'
    resp = resp.form.submit().follow()

    resp = resp.click('Geolocation')
    assert resp.form['geocoding-services-base-url'].value == 'https://nominatim.org'
    assert pub.get_geocoding_service_url() == 'https://nominatim.org/search'
    resp = resp.form.submit().follow()

    pub.site_options.set('options', 'reverse_geocoding_service_url', 'https://nominatim.example.org/reverse')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = resp.click('Geolocation')
    assert 'System settings are currently forcing geocoding URLs' in resp.text


def test_settings_permissions(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role1 = pub.role_class(name='foobar1')
    role1.store()
    role2 = pub.role_class(name='foobar2')
    role2.store()
    role3 = pub.role_class(name='foobar3')
    role3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/admin-permissions')
    # assert all first checkboxes are unchecked
    assert not resp.forms[0]['permissions$c-0-0'].checked
    assert not resp.forms[0]['permissions$c-1-0'].checked
    assert not resp.forms[0]['permissions$c-2-0'].checked

    role2.allows_backoffice_access = True
    role2.store()
    resp = app.get('/backoffice/settings/admin-permissions')
    assert not resp.forms[0]['permissions$c-0-0'].checked
    assert resp.forms[0]['permissions$c-1-0'].checked
    assert not resp.forms[0]['permissions$c-2-0'].checked

    resp.forms[0]['permissions$c-0-0'].checked = True
    resp.forms[0]['permissions$c-1-0'].checked = False
    resp = resp.forms[0].submit()
    assert pub.role_class.get(role1.id).allows_backoffice_access is True
    assert pub.role_class.get(role2.id).allows_backoffice_access is False

    # give some roles access to the forms workshop (2nd checkbox) and to the
    # workflows workshop (4th)
    resp = app.get('/backoffice/settings/admin-permissions')
    resp.forms[0]['permissions$c-1-1'].checked = True
    resp.forms[0]['permissions$c-2-1'].checked = True
    resp.forms[0]['permissions$c-2-3'].checked = True
    resp = resp.forms[0].submit()
    pub.reload_cfg()
    assert set(pub.cfg['admin-permissions']['forms']) == {role2.id, role3.id}
    assert set(pub.cfg['admin-permissions']['workflows']) == {role3.id}

    # remove accesses
    resp = app.get('/backoffice/settings/admin-permissions')
    resp.forms[0]['permissions$c-1-1'].checked = False
    resp.forms[0]['permissions$c-2-1'].checked = False
    resp.forms[0]['permissions$c-2-3'].checked = False
    resp = resp.forms[0].submit()
    pub.reload_cfg()
    assert pub.cfg['admin-permissions']['forms'] == []
    assert pub.cfg['admin-permissions']['workflows'] == []


def test_postgresql_settings(pub):
    create_superuser(pub)

    database = pub.cfg['postgresql']['database']
    assert pub.cfg['postgresql'].get('port') is None

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/postgresql')
    assert resp.form['database'].value == database
    assert resp.form['port'].value == ''
    resp = resp.form.submit()
    assert pub.cfg['postgresql']['port'] is None

    pub.cfg['postgresql']['port'] = 5432
    pub.write_cfg()
    resp = app.get('/backoffice/settings/postgresql')
    assert resp.form['port'].value == '5432'
    resp = resp.form.submit()
    assert pub.cfg['postgresql']['port'] == 5432

    resp = app.get('/backoffice/settings/postgresql')
    resp.form['port'] = ''
    resp = resp.form.submit()
    assert pub.cfg['postgresql']['port'] is None

    pub.cfg['postgresql']['port'] = '5432'  # from an old convert-to-sql (before #10170)
    pub.write_cfg()
    resp = app.get('/backoffice/settings/postgresql')
    assert resp.form['port'].value == '5432'
    resp = resp.form.submit()
    assert pub.cfg['postgresql']['port'] == 5432


def test_i18n(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/language')
    assert resp.form['language'].options == [
        ('English', True, 'English'),
        ('French', False, 'French'),
        ('German', False, 'German'),
    ]
    resp.form['multilinguism'].checked = True
    resp.form['languages$element0'].checked = True
    resp = resp.form.submit('submit')

    # check language selection is now fixed
    resp = app.get('/backoffice/settings/language')
    assert resp.form['language'].options == [('English', True, 'English')]

    # check empty languages
    resp.form['languages$element0'].checked = False
    resp = resp.form.submit('submit')
    pub.reload_cfg()
    assert pub.cfg['language']['languages'] == ['en']

    # check site language is always included
    resp = app.get('/backoffice/settings/language')
    resp.form['languages$element0'].checked = False
    resp.form['languages$element1'].checked = True
    resp = resp.form.submit('submit')
    pub.reload_cfg()
    assert set(pub.cfg['language']['languages']) == {'en', 'fr'}


def test_submission_channels(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/backoffice-submission')
    resp.form['include-in-global-listing'].checked = True
    resp = resp.form.submit('submit')

    pub.reload_cfg()
    assert pub.cfg['submission-channels']['include-in-global-listing'] is True

    resp = app.get('/backoffice/settings/backoffice-submission')
    assert resp.form['include-in-global-listing'].checked


def test_backoffice_submission(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/backoffice-submission')
    resp.form['redirect'] = 'https://example.net'
    resp = resp.form.submit('submit')

    pub.reload_cfg()
    assert pub.cfg['backoffice-submission']['redirect'] == 'https://example.net'

    resp = app.get('/backoffice/settings/backoffice-submission')
    assert resp.form['redirect'].value == 'https://example.net'


def test_hobo_locked_settings(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/settings/')
    resp = app.get('/backoffice/settings/sitename')
    assert not resp.pyquery('#form_sitename').attr.readonly
    hobo_json_path = os.path.join(pub.app_dir, 'hobo.json')
    try:
        with open(hobo_json_path, 'w'):
            resp = app.get('/backoffice/settings/sitename')
            assert resp.pyquery('#form_sitename').attr.readonly
    finally:
        os.unlink(hobo_json_path)
