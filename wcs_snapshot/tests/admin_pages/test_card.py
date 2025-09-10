import datetime
import os
import xml.etree.ElementTree as ET

import pytest
from pyquery import PyQuery
from webtest import Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.formdef import FormDef
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.testdef import TestDef, TestResults
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_cards_list(pub):
    create_superuser(pub)

    role = pub.role_class(name='foobar')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    carddef2 = CardDef()
    carddef2.name = 'card title 2'
    carddef2.fields = []
    carddef2.store()

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store()
    cat2 = CardDefCategory(name='Bar')
    cat2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/')
    assert '<h2>Misc</h2>' not in resp.text
    assert '<h2>Foo</h2>' not in resp.text
    assert '<h2>Bar</h2>' not in resp.text

    carddef.category = cat2
    carddef.store()
    resp = app.get('/backoffice/cards/')
    assert '<h2>Misc</h2>' in resp.text
    assert '<h2>Foo</h2>' not in resp.text
    assert '<h2>Bar</h2>' in resp.text

    carddef2.category = cat
    carddef2.store()
    resp = app.get('/backoffice/cards/')
    assert '<h2>Misc</h2>' not in resp.text
    assert '<h2>Foo</h2>' in resp.text
    assert '<h2>Bar</h2>' in resp.text


def test_cards_list_category_fold(pub):
    create_superuser(pub)
    role = pub.role_class(name='foobar')
    role.store()

    CardDefCategory.wipe()
    cat1 = CardDefCategory(name='Foo')
    cat1.store()
    cat2 = CardDefCategory(name='Bar')
    cat2.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.category_id = cat1.id
    carddef.fields = []
    carddef.store()

    carddef = CardDef()
    carddef.name = 'second card title'
    carddef.category_id = cat2.id
    carddef.fields = []
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/')
    assert resp.pyquery('.foldable:not(.folded)').length == 2
    pref_name = resp.pyquery('.foldable:not(.folded)')[0].attrib['data-section-folded-pref-name']

    # set preference
    app.post_json('/api/user/preferences', {pref_name: True}, status=200)

    resp = app.get('/backoffice/cards/')
    assert resp.pyquery('.foldable:not(.folded)').length == 1
    assert resp.pyquery('.foldable.folded').length == 1
    assert resp.pyquery('.foldable.folded')[0].attrib['data-section-folded-pref-name'] == pref_name


def test_cards_new(pub):
    CardDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/')
    resp = resp.click('New Card Model')
    resp.form['name'] = 'card title'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/cards/1/'
    resp = resp.follow()
    assert resp.pyquery('#appbar h2').text() == 'card title'
    assert CardDef.get(1).workflow_id is None
    assert CardDef.get(1).disabled is False


def test_cards_delete(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()
    carddef2 = CardDef()
    carddef2.name = 'card title'
    carddef2.fields = []
    carddef2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'foo'
    custom_view.formdef = carddef
    custom_view.store()
    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'foo'
    custom_view2.formdef = carddef2
    custom_view2.store()
    custom_view3 = pub.custom_view_class()
    custom_view3.title = 'foo'
    custom_view3.formdef = formdef
    custom_view3.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {}
    carddata.store()

    app = login(get_app(pub))
    resp = app.get('http://example.net/backoffice/cards/1/')
    resp = resp.click('Delete')
    assert 'Deletion is not possible as there are cards.' in resp
    carddef.data_class().wipe()
    resp = app.get('http://example.net/backoffice/cards/1/')
    resp = resp.click('Delete')

    resp = resp.form.submit('submit')
    assert CardDef.count() == 1
    assert CardDef.select()[0].id == carddef2.id
    assert pub.custom_view_class.count() == 2
    assert pub.custom_view_class.get(custom_view2.id)
    assert pub.custom_view_class.get(custom_view3.id)

    carddata.remove_self()  # don't keep leftovers


def test_cards_in_use_delete(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='0', label='string', data_source={'type': 'carddef:card-title'}),
    ]
    formdef.store()

    create_superuser(pub)

    app = login(get_app(pub))
    resp = app.get('http://example.net/backoffice/cards/1/')
    resp = resp.click('Delete')
    assert 'Deletion is not possible as it is still used as datasource.' in resp.text
    assert 'delete-button' not in resp.text

    formdef.fields = []
    formdef.store()
    resp = app.get('http://example.net/backoffice/cards/1/')
    resp = resp.click('Delete')
    assert 'delete-button' in resp.text


def test_cards_duplicate(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('http://example.net/backoffice/cards/1/')
    resp = resp.click('Duplicate')
    assert resp.form['name'].value == 'card title (copy)'
    resp = resp.form.submit('submit')
    assert CardDef.get(2).name == 'card title (copy)'
    assert CardDef.get(2).url_name == 'card-title-copy'
    assert CardDef.get(2).disabled is False


def test_card_workflow_change(pub):
    AfterJob.wipe()
    role = pub.role_class(name='foobar')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow Two')
    workflow.add_status('plop')
    workflow.store()

    CardDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/')
    resp = resp.click('New Card Model')
    resp.form['name'] = 'card title'
    resp = resp.form.submit()
    resp = resp.follow()
    resp = resp.click(href='workflow', index=1)
    assert resp.form['workflow_id'].options[0][2] == 'Default (cards)'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('.afterjob').text() == 'completed'
    AfterJob.wipe()
    resp = resp.click('Back')
    assert CardDef.select()[0].workflow_id is None

    carddata = CardDef.select()[0].data_class()()
    carddata.status = 'wf-recorded'
    carddata.store()

    resp = resp.click(href='workflow', index=1)
    resp.form['workflow_id'] = '%s' % workflow.id
    resp = resp.form.submit('submit')
    assert (
        resp.location
        == 'http://example.net/backoffice/cards/1/workflow-status-remapping?new=%s' % workflow.id
    )
    resp = resp.follow()
    resp.form['mapping-recorded'] = 'plop'
    resp.form['mapping-deleted'] = 'plop'
    resp = resp.form.submit('submit')
    assert AfterJob.count() == 1
    job = AfterJob.select()[0]
    assert job.status == 'completed'
    resp = resp.follow()  # -> to job processing page
    resp = resp.click('Back')
    assert resp.pyquery('[href="workflow"] .offset').text() == 'Workflow Two'
    AfterJob.wipe()

    resp = resp.click(href='workflow', index=1)
    resp.form['workflow_id'] = ''
    resp = resp.form.submit('submit')
    assert (
        resp.location
        == 'http://example.net/backoffice/cards/1/workflow-status-remapping?new=%s' % '_carddef_default'
    )
    resp = resp.follow()
    resp.form['mapping-1'] = 'Recorded'
    resp = resp.form.submit('submit').follow()


def test_card_workflow_link(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'form title'
    carddef.fields = []
    carddef.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert '/backoffice/workflows/_carddef_default/' in resp.text

    carddef.workflow_id = 42
    carddef.store()
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert '/backoffice/workflows/_unknown/' not in resp.text

    carddef.workflow = workflow
    carddef.store()

    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert '/backoffice/workflows/%s/' % workflow.id in resp.text

    # check workflow link is not displayed if user has no access right
    pub.cfg['admin-permissions'] = {'workflows': ['x']}  # block access
    pub.write_cfg()
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert '/backoffice/workflows/%s/' % workflow.id not in resp.text


def assert_option_display(resp, label, value):
    assert [
        PyQuery(x).parent().find('.value')
        for x in resp.pyquery('.optionslist li .label')
        if PyQuery(x).text() == label
    ][0].text() == value


def test_card_templates(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='test'),
    ]
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {'1': 'bar'}
    carddata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')

    assert_option_display(resp, 'Templates', 'None')
    assert resp.pyquery('[href="options/templates"]').attr.rel == ''  # no popup
    resp = resp.click('Templates')
    resp.form['digest_template'] = 'X{{form_var_test}}Y'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/cards/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Templates', 'Custom')
    carddef = CardDef.get(carddef.id)
    assert carddef.digest_templates['default'] == 'X{{form_var_test}}Y'
    assert carddef.lateral_template is None
    assert carddef.submission_lateral_template is None
    assert 'Existing cards will be updated in the background.' in resp.text
    # after jobs are run synchronously in tests
    carddata.refresh_from_storage()
    assert carddata.digests == {'default': 'XbarY'}

    resp = app.get('/backoffice/cards/1/options/templates')
    resp.form['lateral_template'] = 'X{{form_var_test}}Y'
    resp.form['submission_lateral_template'] = 'X{{form_var_test}}YZ'
    resp = resp.form.submit().follow()
    assert_option_display(resp, 'Templates', 'Custom')
    carddef = CardDef.get(carddef.id)
    assert carddef.digest_templates['default'] == 'X{{form_var_test}}Y'
    assert carddef.lateral_template == 'X{{form_var_test}}Y'
    assert carddef.submission_lateral_template == 'X{{form_var_test}}YZ'
    assert 'Existing cards will be updated in the background.' not in resp.text


def test_card_id_template(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='test'),
    ]
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {'1': 'bar'}
    carddata.just_created()
    carddata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')
    resp = resp.click('Templates')
    assert 'Identifier cannot be modified if there are existing cards.' in resp.text

    carddef.data_class().wipe()

    resp = app.get('/backoffice/cards/1/')
    resp = resp.click('Templates')
    assert 'Identifier cannot be modified if there are existing cards.' not in resp.text
    resp.form['id_template'] = 'X{{form_var_test}}Y'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/cards/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Templates', 'Custom')
    carddef = CardDef.get(carddef.id)
    assert carddef.id_template == 'X{{form_var_test}}Y'

    carddata = carddef.data_class()()
    carddata.data = {'1': 'bar'}
    carddata.just_created()
    carddata.store()
    assert carddata.id_display == 'XbarY'

    # check option is not advertised if disabled
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-card-identifier-template', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/cards/1/')
    resp = resp.click('Templates')
    assert 'id_template' not in resp.text

    # check a severe warning is displayed on field removal
    resp = app.get(carddef.fields[0].get_admin_url() + 'delete')
    assert 'This field may be used in the card custom identifiers' in resp.pyquery('.errornotice').text()


def test_card_digest_template(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'X{{ form_var_foo }}Y'}
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {'1': 'bar'}
    carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-1': True, 'filter-1-value': 'FOO'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    # carddef not used in formdef, it's ok to empty digest_template
    FormDef.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/%s/options/templates' % carddef.id)
    resp.form['digest_template'] = ''
    resp = resp.form.submit().follow()
    carddef = CardDef.get(carddef.id)
    assert carddef.digest_templates['default'] is None
    assert 'Existing cards will be updated in the background.' in resp.text

    # afterjobs are actually run synchronously during tests; we don't have
    # to wait to check the digest has been updated:
    assert carddef.data_class().get(carddata.id).digests['default'] is None

    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()

    # a formdef using the carddef as datasource
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test card def on data source'
    formdef.fields = [fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})]
    formdef.store()

    # carddef used in formdef, can not empty digest_template
    resp = app.get('/backoffice/cards/%s/options/templates' % carddef.id)
    resp.form['digest_template'] = ''
    resp = resp.form.submit()
    assert 'Can not empty digest template: this card model is used as data source in some forms.' in resp.text
    carddef = CardDef.get(carddef.id)
    assert carddef.digest_templates['default'] is not None

    # error: not stored, and no after jobs
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert 'Existing cards will be updated in the background.' not in resp.text


def test_card_category(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store()
    cat = CardDefCategory(name='Bar')
    cat.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')
    assert '<span class="label">Category</span> <span class="value">None</span>' in resp.text
    assert '<span class="label">Category</span> <span class="value">Foo</span>' not in resp.text
    assert '<span class="label">Category</span> <span class="value">Bar</span>' not in resp.text
    resp = resp.click(href='category')
    resp.forms[0].submit('cancel')
    assert CardDef.get(carddef.id).category_id is None

    resp = app.get('/backoffice/cards/1/')
    assert '<span class="label">Category</span> <span class="value">None</span>' in resp.text
    assert '<span class="label">Category</span> <span class="value">Foo</span>' not in resp.text
    assert '<span class="label">Category</span> <span class="value">Bar</span>' not in resp.text
    resp = resp.click(href='category')
    resp.forms[0]['category_id'] = cat.id
    resp.forms[0].submit('submit')
    assert CardDef.get(carddef.id).category_id == cat.id

    resp = app.get('/backoffice/cards/1/')
    assert '<span class="label">Category</span> <span class="value">None</span>' not in resp.text
    assert '<span class="label">Category</span> <span class="value">Foo</span>' not in resp.text
    assert '<span class="label">Category</span> <span class="value">Bar</span>' in resp.text


def test_card_user_support(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.backoffice_submission_roles = ['1']
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')
    assert '<span class="label">Submission</span> <span class="value">Default</span>' in resp.text
    resp = resp.click(href='options/backoffice-submission')
    assert resp.forms[0]['submission_user_association'].value == 'none'
    resp.forms[0]['submission_user_association'].value = 'any'
    resp.forms[0].submit('submit')
    assert CardDef.get(carddef.id).user_support == 'optional'

    resp = app.get('/backoffice/cards/1/')
    assert '<span class="label">Submission</span> <span class="value">Custom</span>' in resp.text


def test_card_delete_field_existing_data(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.CommentField(id='2', label='comment field'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'hello'}
    carddata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/%s/fields/1/delete' % carddef.id)
    assert 'You are about to remove the "1st field" field.' in resp.text
    assert 'Warning: this field data will be permanently deleted from existing cards.' in resp.text
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/cards/1/fields/#fieldId_2'
    resp = resp.follow()
    carddef.refresh_from_storage()
    assert len(carddef.fields) == 1


def test_card_custom_view_data_source(pub):
    user = create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    user.roles = [role.id]
    user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    for i in range(3):
        carddata = carddef.data_class()()
        carddata.just_created()
        if i == 0:
            carddata.data = {'1': 'BAR'}
        else:
            carddata.data = {'1': 'FOO'}
        if i == 1:
            carddata.jump_status('deleted')
        carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-1': True, 'filter-1-value': 'FOO'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='1', label='field', varname='foo'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/fields/1/' % formdef.id)
    assert 'carddef:foo' in [x[0] for x in resp.form['data_source$type'].options]
    assert 'carddef:foo:card-view' in [x[0] for x in resp.form['data_source$type'].options]

    assert len(CardDef.get_data_source_items('carddef:foo')) == 3
    assert len(CardDef.get_data_source_items('carddef:foo:card-view')) == 2

    custom_view.filters = {'filter-1': True, 'filter-1-value': 'FOO', 'filter-status': 'on', 'filter': 'done'}
    custom_view.store()
    assert len(CardDef.get_data_source_items('carddef:foo:card-view')) == 1


def test_carddef_usage(pub):
    user = create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    user.roles = [role.id]
    user.store()

    # the one used as data source
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    # another one
    carddef2 = CardDef()
    carddef2.name = 'foobar'  # url_name startswith 'foo'
    carddef2.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef2.store()

    # custom view
    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'card view'
    resp = resp.forms['save-custom-view'].submit()

    # a formdef using the carddef as datasource
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test card def on data source'
    formdef.fields = [fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})]
    formdef.store()
    # another using the custom view
    formdef2 = FormDef()
    formdef2.name = 'test card def on data source2'
    formdef2.fields = [fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo:card-view'})]
    formdef2.store()
    # another, using the other carddef as datasource
    formdef3 = FormDef()
    formdef3.name = 'test card def on data source2'
    formdef3.fields = [
        fields.ItemField(
            id='1', label='item', data_source={'type': 'carddef:foobar'}
        )  # startswith carddef:foo
    ]
    formdef3.store()

    # user form
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'}))
    user_formdef.store()

    # workflow
    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})
    )
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})
    )

    baz_status = workflow.add_status(name='baz')
    display_form = baz_status.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})
    )

    workflow.store()

    # carddef
    carddef3 = CardDef()
    carddef3.name = 'Baz'
    carddef3.fields = [fields.ItemField(id='1', label='item', data_source={'type': 'carddef:foo'})]
    carddef3.store()

    resp = app.get('http://example.net/backoffice/cards/1/')
    assert 'This card model is used as data source in the following forms' in resp.text
    assert '/backoffice/forms/%s/' % formdef.id in resp.text
    assert '/backoffice/forms/%s/' % formdef2.id in resp.text
    assert '/backoffice/forms/%s/' % formdef3.id not in resp.text  # no, not the good one
    assert '/backoffice/workflows/%s/backoffice-fields/fields/' % workflow.id in resp.text
    assert '/backoffice/workflows/%s/variables/fields/' % workflow.id in resp.text
    assert '/backoffice/workflows/%s/status/1/items/_x/fields/' % workflow.id in resp.text
    assert '/backoffice/settings/users/fields/' in resp.text
    assert '/backoffice/cards/%s/' % carddef3.id in resp.text

    # cleanup
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = []
    user_formdef.store()


def test_card_management_view(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')
    assert 'backoffice/data/foo/' in resp


def test_card_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/cards/', status=403)

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.category_id = cat.id
    carddef.fields = []
    carddef.store()

    cat = CardDefCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/cards/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'card title' not in resp.text  # carddef in that category
    assert 'Bar' not in resp.text  # not yet any form in this category

    resp = resp.click('New Card')
    resp.forms[0]['name'] = 'card in category'
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user
    resp = resp.forms[0].submit().follow()
    new_carddef = CardDef.get_by_urlname('card-in-category')

    # check category select only let choose one
    resp = resp.click(href='/category')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user

    resp = app.get('/backoffice/cards/')
    assert 'Bar' in resp.text  # now there's a form in this category
    assert 'card in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    app.get('/backoffice/cards/categories/', status=403)

    # no import into other category
    carddef_xml = ET.tostring(carddef.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('carddef.wcs', carddef_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    # check access to inspect page
    carddef.workflow_roles = {'_viewer': str(backoffice_role.id)}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()

    resp = app.get(carddata.get_backoffice_url())
    assert 'inspect' not in resp.text
    resp = app.get(carddata.get_backoffice_url() + 'inspect', status=403)

    new_carddef.workflow_roles = {'_viewer': str(backoffice_role.id)}
    new_carddef.store()

    carddata = new_carddef.data_class()()
    carddata.just_created()
    carddata.store()
    resp = app.get(carddata.get_backoffice_url())
    assert 'inspect' in resp.text
    resp = app.get(carddata.get_backoffice_url() + 'inspect')


def test_cards_svg(pub):
    create_superuser(pub)

    CardDefCategory.wipe()
    cat1 = CardDefCategory(name='Foo')
    cat1.store()
    cat2 = CardDefCategory(name='Foo')
    cat2.store()

    ds1 = {'type': 'carddef:card-1-2'}
    ds2 = {'type': 'carddef:card-2-2'}
    ds3 = {'type': 'carddef:card-3-2'}

    BlockDef.wipe()
    block1 = BlockDef()
    block1.name = 'block 1'
    block1.fields = [
        fields.StringField(id='1', label='string', varname='block foo 1', data_source=ds1),
        fields.ItemField(id='2', label='item', varname='block foo 2', data_source=ds1),
        fields.ItemsField(id='3', label='items', varname='block foo 3', data_source=ds1),
        fields.ComputedField(id='4', label='computed', varname='block foo 4', data_source=ds1),
        fields.StringField(id='10', label='string', varname='block fooo 10', data_source=ds2),
        fields.ItemField(id='20', label='item', varname='block fooo 20', data_source=ds2),
        fields.ItemsField(id='30', label='items', varname='block fooo 30', data_source=ds2),
        fields.ComputedField(id='40', label='computed', varname='block fooo 40', data_source=ds2),
    ]
    block1.store()
    block2 = BlockDef()
    block2.name = 'block 2'
    block2.fields = [
        fields.StringField(id='1', label='string', varname='block bar 1', data_source=ds2),
        fields.ItemField(id='2', label='item', varname='block bar 2', data_source=ds2),
        fields.ItemsField(id='3', label='items', varname='block bar 3', data_source=ds2),
        fields.ComputedField(id='4', label='computed', varname='block bar 4', data_source=ds2),
    ]
    block2.store()
    block3 = BlockDef()
    block3.name = 'block 3'
    block3.fields = [
        fields.StringField(id='1', label='string', varname='block baz 1', data_source=ds3),
        fields.ItemField(id='2', label='item', varname='block baz 2', data_source=ds3),
        fields.ItemsField(id='3', label='items', varname='block baz 3', data_source=ds3),
        fields.ComputedField(id='4', label='computed', varname='block baz 4', data_source=ds3),
    ]
    block3.store()

    CardDef.wipe()

    carddef11 = CardDef()
    carddef11.name = 'card 1-1'
    carddef11.category_id = cat1.id
    carddef11.fields = [
        fields.StringField(id='1', label='string', varname='foo 1', data_source=ds1),
        fields.ItemField(id='2', label='item', varname='foo 2', data_source=ds1),
        fields.ItemsField(id='3', label='items', varname='foo 3', data_source=ds1),
        fields.ComputedField(id='4', label='computed', varname='foo 4', data_source=ds1),
        fields.BlockField(id='5', label='block', block_slug=block1.slug),
        fields.StringField(id='10', label='string', varname='fooo 10', data_source=ds2),
        fields.ItemField(id='20', label='item', varname='fooo 20', data_source=ds2),
        fields.ItemsField(id='30', label='items', varname='fooo 30', data_source=ds2),
        fields.ComputedField(id='40', label='computed', varname='fooo 40', data_source=ds2),
        fields.BlockField(id='50', label='block', block_slug=block2.slug),
    ]
    carddef11.store()
    carddef12 = CardDef()
    carddef12.name = 'card 1-2'
    carddef12.category_id = cat1.id
    carddef12.fields = []
    carddef12.store()
    carddef13 = CardDef()
    carddef13.name = 'card 1-3'
    carddef13.category_id = cat1.id
    carddef13.fields = []
    carddef13.store()

    carddef21 = CardDef()
    carddef21.name = 'card 2-1'
    carddef21.category_id = cat2.id
    carddef21.fields = [
        fields.StringField(id='1', label='string', varname='bar 1', data_source=ds2),
        fields.ItemField(id='2', label='item', varname='bar 2', data_source=ds2),
        fields.ItemsField(id='3', label='items', varname='bar 3', data_source=ds2),
        fields.ComputedField(id='4', label='computed', varname='bar 4', data_source=ds2),
        fields.BlockField(id='5', label='block', block_slug=block2.slug),
    ]
    carddef21.store()
    carddef22 = CardDef()
    carddef22.name = 'card 2-2'
    carddef22.category_id = cat2.id
    carddef22.fields = []
    carddef22.store()

    carddef31 = CardDef()
    carddef31.name = 'card 3-1'
    carddef31.fields = [
        fields.StringField(id='1', label='string', varname='baz 1', data_source=ds3),
        fields.ItemField(id='2', label='item', varname='baz 2', data_source=ds3),
        fields.ItemsField(id='3', label='items', varname='baz 3', data_source=ds3),
        fields.ComputedField(id='4', label='computed', varname='baz 4', data_source=ds3),
        fields.BlockField(id='5', label='block', block_slug=block3.slug),
    ]
    carddef31.store()
    carddef32 = CardDef()
    carddef32.name = 'card 3-2'
    carddef32.fields = []
    carddef32.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/cards/svg')
    # cards
    assert '<title>card_card_1_1</title' in resp
    assert (
        'xlink:href="http://example.net/backoffice/cards/%s/" xlink:title="&lt;card&gt;card 1&#45;1"'
        % carddef11.id
        in resp
    )
    assert '<title>card_card_1_2</title' in resp
    assert '<title>card_card_1_3</title' not in resp
    assert '<title>card_card_2_1</title' in resp
    assert '<title>card_card_2_2</title' in resp
    assert '<title>card_card_3_1</title' in resp
    assert '<title>card_card_3_2</title' in resp
    # and relations
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_1_2</title>') == 8
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_2_2</title>') == 12
    assert resp.text.count('<title>card_card_2_1&#45;&gt;card_card_2_2</title>') == 8
    assert resp.text.count('<title>card_card_3_1&#45;&gt;card_card_3_2</title>') == 8

    resp = app.get('/backoffice/cards/svg?show-orphans=on')
    assert '<title>card_card_1_3</title' in resp

    resp = app.get('/backoffice/cards/categories/%s/svg' % cat1.id)
    # cards
    assert '<title>card_card_1_1</title' in resp
    assert (
        'xlink:href="http://example.net/backoffice/cards/%s/" xlink:title="&lt;card&gt;card 1&#45;1"'
        % carddef11.id
        in resp
    )
    assert '<title>card_card_1_2</title' in resp
    assert '<title>card_card_1_3</title' not in resp
    assert '<title>card_card_2_1</title' not in resp
    assert '<title>card_card_2_2</title' not in resp
    assert '<title>card_card_3_1</title' not in resp
    assert '<title>card_card_3_2</title' not in resp
    # and relations
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_1_2</title>') == 8
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_2_2</title>') == 0
    assert resp.text.count('<title>card_card_2_1&#45;&gt;card_card_2_2</title>') == 0
    assert resp.text.count('<title>card_card_3_1&#45;&gt;card_card_3_2</title>') == 0

    resp = app.get('/backoffice/cards/categories/%s/svg?show-orphans=on' % cat1.id)
    assert '<title>card_card_1_3</title' in resp

    resp = app.get('/backoffice/cards/categories/%s/svg' % cat2.id)
    # cards
    assert '<title>card_card_1_1</title' not in resp
    assert '<title>card_card_1_2</title' not in resp
    assert '<title>card_card_2_1</title' in resp
    assert '<title>card_card_2_2</title' in resp
    assert '<title>card_card_3_1</title' not in resp
    assert '<title>card_card_3_2</title' not in resp
    # and relations
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_1_2</title>') == 0
    assert resp.text.count('<title>card_card_1_1&#45;&gt;card_card_2_2</title>') == 0
    assert resp.text.count('<title>card_card_2_1&#45;&gt;card_card_2_2</title>') == 8
    assert resp.text.count('<title>card_card_3_1&#45;&gt;card_card_3_2</title>') == 0


def test_card_edit_field_required(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [fields.StringField(id='1', label='1st field')]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get(f'{carddef.get_admin_url()}fields/1/')
    assert [x[0] for x in resp.form['required'].options] == ['required', 'optional']
    resp.form['required'] = 'optional'
    resp = resp.form.submit('submit')
    carddef.refresh_from_storage()
    assert carddef.fields[0].required == 'optional'

    resp = app.get(f'{carddef.get_admin_url()}inspect')
    assert resp.pyquery('.parameter-required').text() == 'Required: No'


def test_card_edit_field_warnings(pub):
    create_superuser(pub)

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'ignore-hard-limits', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [fields.StringField(id='%d' % i, label='field %d' % i) for i in range(1, 10)]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'more than 200 fields' not in resp.text
    assert 'first field should be of type "page"' not in resp.text

    carddef.fields.append(fields.PageField(id='1000', label='page'))
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'more than 200 fields' not in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert resp.pyquery('#new-field')

    carddef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(10, 210)])
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'more than 200 fields' in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert '>Duplicate<' in resp.text

    carddef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(210, 410)])
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'This card model contains 410 fields.' in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert not resp.pyquery('#new-field')
    assert '>Duplicate<' not in resp.text


def test_card_edit_field_infonotices(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'There are no fields configured to be shown in listings.' not in resp.text

    carddef.fields = [fields.StringField(id='0', label='field')]
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'There are no fields configured to be shown in listings.' in resp.text

    carddef.fields = [fields.StringField(id='1', label='field', display_locations=['listings'])]
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'There are no fields configured to be shown in listings.' not in resp.text

    carddef.fields = [fields.PageField(id='2', label='field')]
    carddef.store()
    resp = app.get('/backoffice/cards/%s/fields/' % carddef.id)
    assert 'There are no fields configured to be shown in listings.' not in resp.text


def test_cards_last_test_results(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    TestDef.wipe()
    testdef = TestDef()
    testdef.object_type = carddef.get_table_name()
    testdef.object_id = str(carddef.id)
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')
    assert 'Last tests run' not in resp.text

    TestResults.wipe()
    test_results = TestResults()
    test_results.object_type = carddef.get_table_name()
    test_results.object_id = str(carddef.id)
    test_results.timestamp = datetime.datetime(2023, 7, 3, 14, 30)
    test_results.success = True
    test_results.reason = ''
    test_results.results = []
    test_results.store()

    resp = app.get('/backoffice/cards/1/')
    assert 'Last tests run: 2023-07-03 14:30' in resp.text
    assert resp.pyquery('.test-success')
    assert not resp.pyquery('.test-failure')

    resp = resp.click('Last tests run')
    assert 'Result #%s' % test_results.id in resp.text


def test_cards_management_options(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='test'),
    ]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/cards/1/')

    # Misc management
    assert_option_display(resp, 'Management', 'Default')
    resp = resp.click('Management', href='options/management')
    assert resp.forms[0]['management_sidebar_items$elementgeneral'].checked is True
    assert resp.forms[0]['management_sidebar_items$elementdownload-files'].checked is False
    resp.forms[0]['management_sidebar_items$elementdownload-files'].checked = True
    resp = resp.forms[0].submit().follow()
    assert_option_display(resp, 'Management', 'Custom')
    assert 'general' in CardDef.get(1).management_sidebar_items
    assert 'download-files' in CardDef.get(1).management_sidebar_items

    resp = resp.click('Management', href='options/management')
    resp.forms[0]['management_sidebar_items$elementgeneral'].checked = False
    resp = resp.forms[0].submit().follow()
    assert 'general' not in CardDef.get(1).management_sidebar_items

    resp = resp.click('Management', href='options/management')
    resp.forms[0]['management_sidebar_items$elementgeneral'].checked = True
    resp.forms[0]['management_sidebar_items$elementdownload-files'].checked = False
    assert resp.forms[0]['management_sidebar_items$elementuser'].checked is True
    resp = resp.forms[0].submit().follow()
    assert CardDef.get(1).management_sidebar_items == {'__default__'}

    assert_option_display(resp, 'Management', 'Default')
    resp = resp.click('Management', href='options/management')
    assert resp.form['history_pane_default_mode'].value == 'collapsed'
    resp = resp.form.submit().follow()
    assert_option_display(resp, 'Templates', 'None')
    resp = resp.click('Management', href='options/management')
    resp.form['history_pane_default_mode'].value = 'expanded'
    resp = resp.form.submit().follow()
    assert_option_display(resp, 'Templates', 'None')
    resp = resp.click('Management', href='options/management')
    assert resp.form['history_pane_default_mode'].value == 'expanded'


def test_card_documentation(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = FormDef()
    carddef.name = 'card title'
    carddef.fields = [fields.BoolField(id='1', label='Bool')]
    carddef.store()

    app = login(get_app(pub))

    resp = app.get(carddef.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(carddef.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    carddef.refresh_from_storage()
    assert carddef.documentation == '<p>doc</p>'
    resp = app.get(carddef.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(carddef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation[hidden]')
    assert resp.pyquery('#sidebar[hidden]')
    resp = app.post_json(carddef.get_admin_url() + 'fields/1/update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    carddef.refresh_from_storage()
    assert carddef.fields[0].documentation == '<p>doc</p>'
    resp = app.get(carddef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation:not([hidden])')
    assert resp.pyquery('#sidebar:not([hidden])')


def test_cards_by_slug(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    carddef = CardDef()
    carddef.name = 'card title'
    carddef.store()

    assert app.get('/backoffice/cards/by-slug/card-title').location == carddef.get_admin_url()
    assert app.get('/backoffice/cards/by-slug/xxx', status=404)
