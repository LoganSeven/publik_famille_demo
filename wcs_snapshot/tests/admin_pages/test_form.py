import datetime
import io
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET

import pytest
import responses
from django.utils.timezone import localtime
from pyquery import PyQuery
from webtest import Upload

from wcs import fields, workflow_tests
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category, DataSourceCategory, WorkflowCategory
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.errors import ConnectionError
from wcs.qommon.http_request import HTTPRequest
from wcs.sql_criterias import Equal
from wcs.testdef import TestDef, TestResults, WebserviceResponse
from wcs.workflow_tests import WorkflowTests
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_role, create_superuser


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


@pytest.fixture
def formdef(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    return formdef


def test_forms(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    assert 'You first have to define roles.' in resp.text
    assert 'New Form' not in resp.text


def test_forms_new(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    create_role(pub)

    FormDef.wipe()
    # create a new form
    resp = app.get('/backoffice/forms/')
    assert 'New Form' in resp.text
    resp = resp.click('New Form')
    resp.forms[0]['name'] = 'form title'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert resp.pyquery('#appbar h2').text() == 'form title'

    # makes sure the data has been correctly saved
    formdef = FormDef.get(1)
    assert formdef.name == 'form title'
    assert formdef.url_name == 'form-title'
    assert formdef.fields == []
    assert formdef.disabled is True

    # check max title length
    resp = app.get('/backoffice/forms/')
    resp = resp.click('New Form')
    resp.forms[0]['name'] = 'form title ' * 30
    resp = resp.forms[0].submit()
    assert resp.pyquery('#form_error_name').text() == 'Too long, value must be at most 250 characters.'

    # check workflow selection is available when there are workflows
    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.store()
    workflow = Workflow(name='Workflow Two')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.store()
    workflow3 = Workflow(name='Workflow Three')  # without any status
    workflow3.store()

    resp = app.get('/backoffice/forms/')
    resp = resp.click('New Form')
    resp.forms[0]['name'] = 'second form'
    with pytest.raises(ValueError):
        resp.forms[0]['workflow_id'].select(text='Workflow Three')
    resp.forms[0]['workflow_id'].select(text='Workflow Two')
    # check select is setup for autocompletion
    assert resp.pyquery('select#form_workflow_id')[0].attrib['data-autocomplete']
    assert 'select2.min.js' in resp.text
    resp = resp.forms[0].submit()
    formdef = FormDef.get(2)
    assert formdef.name == 'second form'
    assert formdef.workflow_id == str(workflow.id)


def test_forms_new_popup(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    create_role(pub)

    # create a new form
    resp = app.get('/backoffice/forms/')
    assert 'New Form' in resp.text
    resp = resp.click('New Form', extra_environ={'HTTP_X_POPUP': 'true'})
    assert 'popup-content' in resp.text
    resp.forms[0]['name'] = 'form title'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert resp.pyquery('#appbar h2').text() == 'form title'

    # makes sure the data has been correctly saved
    formdef = FormDef.get(1)
    assert formdef.name == 'form title'
    assert formdef.url_name == 'form-title'
    assert formdef.fields == []
    assert formdef.disabled is True


def test_forms_new_duplicated_name(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    create_role(pub)

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    resp = app.get('/backoffice/forms/')
    resp = resp.click('New Form')
    resp.forms[0]['name'] = 'form title'
    resp = resp.forms[0].submit()
    assert resp.pyquery('.error').text() == 'This name is already used.'


def assert_option_display(resp, label, value):
    assert [
        PyQuery(x).parent().find('.value')
        for x in resp.pyquery('.optionslist li .label')
        if PyQuery(x).text() == label
    ][0].text() == value


def test_forms_edit_confirmation_page(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # confirmation page
    assert_option_display(resp, 'Confirmation Page', 'Enabled')
    resp = resp.click('Confirmation Page')
    assert resp.forms[0]['confirmation'].checked
    resp.forms[0]['confirmation'].checked = False
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Confirmation Page', 'Disabled')
    assert FormDef.get(1).confirmation is False

    # try cancel button
    resp = resp.click('Confirmation Page')
    assert resp.forms[0]['confirmation'].checked is False
    resp.forms[0]['confirmation'].checked = True
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Confirmation Page', 'Disabled')
    assert FormDef.get(1).confirmation is False


def test_forms_edit_limit_one_form(pub, formdef):
    create_superuser(pub)
    formdef = FormDef.get(1)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Limit to one form
    assert formdef.roles is None
    resp = resp.click('User Roles')
    assert resp.forms[0]['only_allow_one'].checked is False
    resp.forms[0]['only_allow_one'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    # check warning is displayed
    assert 'The single form option concerns logged in users only' in resp.text
    formdef.refresh_from_storage()
    assert formdef.only_allow_one is True

    formdef.only_allow_one = False
    formdef.roles = ['logged-users']
    formdef.store()
    resp = resp.click('User Roles')
    resp.forms[0]['only_allow_one'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    # check warning is not displayed
    assert 'The single form option concerns logged in users only' not in resp.text
    formdef.refresh_from_storage()
    assert formdef.only_allow_one is True


def test_forms_edit_management(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Misc management
    assert_option_display(resp, 'Management', 'Default')
    resp = resp.click('Management', href='options/management')
    assert resp.forms[0]['management_sidebar_items$elementgeneral'].checked is True
    assert resp.forms[0]['management_sidebar_items$elementdownload-files'].checked is False
    resp.forms[0]['management_sidebar_items$elementdownload-files'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Management', 'Custom')
    assert 'general' in FormDef.get(1).management_sidebar_items
    assert 'download-files' in FormDef.get(1).management_sidebar_items

    resp = resp.click('Management', href='options/management')
    resp.forms[0]['management_sidebar_items$elementgeneral'].checked = False
    resp = resp.forms[0].submit().follow()
    assert 'general' not in FormDef.get(1).management_sidebar_items

    resp = resp.click('Management', href='options/management')
    resp.forms[0]['management_sidebar_items$elementgeneral'].checked = True
    resp.forms[0]['management_sidebar_items$elementdownload-files'].checked = False
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).management_sidebar_items == {'__default__'}

    # unselect all
    resp = resp.click('Management', href='options/management')
    for field in resp.forms[0].fields:
        if field.startswith('management_sidebar_items$'):
            resp.forms[0][field].checked = False
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).management_sidebar_items == set()

    resp = resp.click('Management', href='options/management')
    resp.forms[0]['old_but_non_anonymised_warning'].value = '100'
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).old_but_non_anonymised_warning == 100


def test_forms_edit_backoffice_submission(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    assert 'Backoffice submission' not in resp.text
    formdef.backoffice_submission_roles = ['x']
    formdef.store()

    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Backoffice submission', 'Default')
    resp = resp.click('Backoffice submission', href='options/backoffice-submission')
    assert resp.forms[0]['submission_sidebar_items$elementgeneral'].checked is True
    assert resp.forms[0]['submission_sidebar_items$elementsubmission-context'].checked is True
    assert resp.forms[0]['submission_sidebar_items$elementuser'].checked is True
    assert resp.forms[0]['submission_sidebar_items$elementcustom-template'].checked is True
    resp.forms[0]['submission_sidebar_items$elementuser'].checked = False
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Backoffice submission', 'Custom')
    assert 'general' in FormDef.get(1).submission_sidebar_items
    assert 'user' not in FormDef.get(1).submission_sidebar_items

    resp = resp.click('Backoffice submission', href='options/backoffice-submission')
    resp.forms[0]['submission_sidebar_items$elementuser'].checked = True
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).submission_sidebar_items == {'__default__'}

    # unselect all
    resp = resp.click('Backoffice submission', href='options/backoffice-submission')
    for field in resp.forms[0].fields:
        if field.startswith('submission_sidebar_items$'):
            resp.forms[0][field].checked = False
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).submission_sidebar_items == set()

    # make user required
    resp = resp.click('Backoffice submission', href='options/backoffice-submission')
    resp.forms[0]['submission_user_association'].value = 'any-required'
    resp = resp.forms[0].submit()
    assert (
        resp.pyquery('.widget-with-error .error').text()
        == 'As a user is required its selection must be kept in the sidebar.'
    )
    resp.forms[0]['submission_sidebar_items$elementuser'].checked = True
    resp = resp.forms[0].submit().follow()


def test_forms_edit_tracking_code(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Tracking code
    assert_option_display(resp, 'Form Tracking', 'Disabled')
    resp = resp.click('Form Tracking')
    assert resp.forms[0]['enable_tracking_codes'].checked is False
    resp.forms[0]['enable_tracking_codes'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Form Tracking', 'Enabled')
    assert FormDef.get(1).enable_tracking_codes is True

    resp = resp.click('Form Tracking')
    assert resp.forms[0]['drafts_lifespan'].value == ''
    assert resp.forms[0]['drafts_max_per_user'].value == ''
    resp = resp.forms[0].submit().follow()  # check empty value is ok

    resp = resp.click('Form Tracking')
    resp.forms[0]['drafts_lifespan'].value = 'xxx'
    resp = resp.forms[0].submit()
    assert 'Lifespan must be between 2 and 100 days.' in resp
    resp.forms[0]['drafts_lifespan'].value = '120'
    resp = resp.forms[0].submit()
    assert 'Lifespan must be between 2 and 100 days.' in resp
    resp.forms[0]['drafts_lifespan'].value = '5'
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).drafts_lifespan == '5'

    resp = resp.click('Form Tracking')
    resp.forms[0]['drafts_max_per_user'].value = 'xxx'
    resp = resp.forms[0].submit()
    assert 'Maximum must be between 2 and 100 drafts.' in resp
    resp.forms[0]['drafts_max_per_user'].value = '120'
    resp = resp.forms[0].submit()
    assert 'Maximum must be between 2 and 100 drafts.' in resp
    resp.forms[0]['drafts_max_per_user'].value = '1'
    resp = resp.forms[0].submit()
    assert 'Maximum must be between 2 and 100 drafts.' in resp
    resp.forms[0]['drafts_max_per_user'].value = '3'
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).drafts_max_per_user == '3'

    formdef.fields = [
        fields.StringField(id='1', label='VerifyString'),
        fields.DateField(id='2', label='VerifyDate'),
        fields.ItemField(id='3', label='CannotVerify'),
    ]
    formdef.store()
    resp = resp.click('Form Tracking')
    assert '<option value="1">VerifyString</option>' in resp
    assert '<option value="2">VerifyDate</option>' in resp
    assert 'CannotVerify' not in resp
    resp.forms[0]['tracking_code_verify_fields$element0'].value = '1'
    resp = resp.forms[0].submit().follow()
    assert FormDef.get(1).tracking_code_verify_fields == ['1']


def test_forms_edit_captcha(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # CAPTCHA
    assert_option_display(resp, 'CAPTCHA for anonymous users', 'Disabled')
    resp = resp.click('CAPTCHA for anonymous users')
    assert resp.forms[0]['has_captcha'].checked is False
    resp.forms[0]['has_captcha'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'CAPTCHA for anonymous users', 'Enabled')
    assert FormDef.get(1).has_captcha is True


def test_forms_edit_appearance(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Appearance
    assert_option_display(resp, 'Appearance', 'Standard')
    resp = resp.click('Appearance')
    assert resp.forms[0]['appearance_keywords'].value == ''
    resp.forms[0]['appearance_keywords'] = 'foobar'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Appearance', 'foobar')
    assert FormDef.get(1).appearance_keywords == 'foobar'


def test_forms_edit_publication(pub, formdef):
    create_superuser(pub)
    create_role(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Publication
    assert_option_display(resp, 'Online Status', 'Active')
    resp = resp.click('Online Status')
    assert resp.forms[0]['disabled'].checked is False
    resp.forms[0]['disabled'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Online Status', 'Disabled')
    assert FormDef.get(1).disabled is True

    resp = resp.click('Online Status')
    assert resp.forms[0]['disabled'].checked is True
    resp.forms[0]['disabled_redirection'] = 'http://www.example.net'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Online Status', 'Redirected')
    assert FormDef.get(1).disabled is True
    assert FormDef.get(1).disabled_redirection == 'http://www.example.net'

    resp = resp.click('Online Status')
    resp.forms[0]['disabled'].checked = False
    resp.forms[0]['expiration_date$date'] = '2000-01-01'  # this is past(tm)
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Online Status', 'Inactive by date')


def test_form_title_change(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    assert formdef.table_name == f'formdata_{formdef.id}_form_title'

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('change title')
    resp.form['name'] = 'new title'
    resp = resp.form.submit('cancel').follow()

    resp = resp.click('change title')
    assert resp.form['name'].value == 'form title'
    assert 'data-slug-sync' in resp.text
    assert 'change-nevertheless' not in resp.text
    resp.form['name'] = 'new title'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    formdef = FormDef.get(formdef.id)
    assert formdef.name == 'new title'
    assert formdef.url_name == 'form-title'
    assert formdef.table_name == f'formdata_{formdef.id}_form_title'

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('change title')
    assert 'data-slug-sync' not in resp.text
    assert 'change-nevertheless' not in resp.text

    formdef.data_class()().store()
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('change title')
    assert 'change-nevertheless' in resp.text

    formdef2 = FormDef()
    formdef2.name = 'other title'
    formdef2.fields = []
    formdef2.store()

    resp = app.get('/backoffice/forms/%s/' % formdef2.id)
    resp = resp.click('change title')
    assert resp.form['name'].value == 'other title'
    resp.form['url_name'] = formdef.url_name
    resp = resp.form.submit()
    assert 'This identifier is already used.' in resp.text

    resp.form['url_name'] = 'foobar'
    resp = resp.form.submit().follow()
    assert FormDef.get(formdef2.id).url_name == 'foobar'

    resp = app.get('/backoffice/forms/%s/title' % formdef2.id)
    resp.form['name'].value = 'new title'
    resp = resp.form.submit()
    assert 'This name is already used.' in resp.text

    # check a form with a number as first character also gets a proper
    # slug-sync attribute
    formdef = FormDef()
    formdef.name = '2 form title'
    formdef.store()
    resp = app.get(formdef.get_admin_url())
    resp = resp.click('change title')
    assert resp.form['name'].value == '2 form title'
    assert resp.form['url_name'].value == 'n2-form-title'
    assert 'data-slug-sync' in resp.text


def test_form_url_name_change(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/%s/title' % formdef.id)
    assert resp.form['name'].value == 'form title'
    resp.form['name'] = 'new title'
    resp = resp.form.submit(status=302)
    formdef = FormDef.get(formdef.id)
    assert formdef.name == 'new title'
    assert formdef.url_name == 'form-title'

    resp = app.get('/backoffice/forms/%s/title' % formdef.id)
    resp.form['url_name'] = 'new-title'
    resp = resp.form.submit(status=302)
    assert FormDef.get(formdef.id).url_name == 'new-title'

    resp = app.get('/backoffice/forms/%s/title' % formdef.id)
    resp.form['url_name'] = 'New-title'
    resp = resp.form.submit(status=200)
    assert 'wrong format' in resp.text

    formdef.url_name = 'New-title'  # preexisting uppercase
    formdef.store()
    resp = app.get('/backoffice/forms/%s/title' % formdef.id)
    resp.form['url_name'] = 'New-Title'
    resp = resp.form.submit(status=302)
    assert FormDef.get(formdef.id).url_name == 'New-Title'


def test_forms_edit_publication_date(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/options/online_status')
    resp.form['publication_date$date'] = '2020-01-01'
    resp = resp.form.submit()
    assert FormDef.get(formdef.id).publication_date == '2020-01-01 00:00'

    resp = app.get('/backoffice/forms/1/options/online_status')
    assert resp.form['publication_date$date'].value == '2020-01-01'
    resp.form['publication_date$time'] = '12:00'
    resp = resp.form.submit()
    assert FormDef.get(formdef.id).publication_date == '2020-01-01 12:00'

    resp = app.get('/backoffice/forms/1/options/online_status')
    assert resp.form['publication_date$date'].value == '2020-01-01'
    assert resp.form['publication_date$time'].value == '12:00'

    formdef.publication_date = None
    formdef.store()

    resp = app.get('/backoffice/forms/1/options/online_status')
    resp.form['publication_date$time'] = '12:00'
    resp = resp.form.submit()
    assert 'invalid value' in resp


def test_forms_list_publication_date(pub, freezer):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.publication_date = '2024-03-06 00:00'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    assert resp.pyquery('.publication-dates').text() == 'Published from 2024-03-06 00:00'

    formdef.expiration_date = '2024-03-10 00:00'
    formdef.store()
    resp = app.get('/backoffice/forms/')
    assert (
        resp.pyquery('.publication-dates').text() == 'Published from 2024-03-06 00:00 until 2024-03-10 00:00'
    )

    formdef.publication_date = None
    formdef.store()
    resp = app.get('/backoffice/forms/')
    assert resp.pyquery('li.disabled .publication-dates').text() == 'Unpublished since 2024-03-10 00:00'

    freezer.move_to(datetime.date(2024, 2, 1))
    resp = app.get('/backoffice/forms/')
    assert resp.pyquery('li:not(.disabled) .publication-dates').text() == 'Published until 2024-03-10 00:00'


def test_form_category(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Category', 'None')
    resp = resp.click('Category')
    assert 'There are not yet any category.' in resp.text

    Category.wipe()
    cat = Category(name='Foo')
    cat.store()
    cat = Category(name='Bar')
    cat.store()
    resp = app.get('/backoffice/forms/1/')
    assert 'Category' in resp.text
    assert_option_display(resp, 'Category', 'None')
    resp = resp.click('Category')
    assert 'Select a category for this form' in resp.text


def test_form_category_fold(pub):
    create_superuser(pub)
    create_role(pub)

    Category.wipe()
    cat1 = Category(name='Foo')
    cat1.store()
    cat2 = Category(name='Bar')
    cat2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.category_id = cat1.id
    formdef.fields = []
    formdef.store()

    formdef = FormDef()
    formdef.name = 'second form title'
    formdef.category_id = cat2.id
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    assert resp.pyquery('.foldable:not(.folded)').length == 2
    pref_name = resp.pyquery('.foldable:not(.folded)')[0].attrib['data-section-folded-pref-name']

    # set preference
    app.post_json('/api/user/preferences', {pref_name: True}, status=200)

    resp = app.get('/backoffice/forms/')
    assert resp.pyquery('.foldable:not(.folded)').length == 1
    assert resp.pyquery('.foldable.folded').length == 1
    assert resp.pyquery('.foldable.folded')[0].attrib['data-section-folded-pref-name'] == pref_name


def test_form_category_select(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    Category.wipe()
    cat = Category(name='Foo')
    cat.store()
    cat = Category(name='Bar')
    cat.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='category')
    resp = resp.forms[0].submit('cancel')
    assert FormDef.get(formdef.id).category_id is None

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='category')
    resp.forms[0]['category_id'] = str(cat.id)
    resp = resp.forms[0].submit('submit')
    assert FormDef.get(formdef.id).category_id == str(cat.id)


def test_form_workflow(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Workflow', 'Default')

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()
    workflow = Workflow(name='Workflow Two')
    workflow.store()

    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Workflow', 'Default')


def test_form_workflow_change(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()
    workflow = Workflow(name='Workflow Two')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo3-3x', label='bo field'),
    ]
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    # check select is setup for autocompletion
    assert resp.pyquery('select#form_workflow_id')[0].attrib['data-autocomplete']
    assert 'select2.min.js' in resp.text
    resp = resp.forms[0].submit('cancel')
    assert FormDef.get(formdef.id).workflow_id is None

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    # no categories, no optgroup
    assert [x[2] for x in resp.form['workflow_id'].options] == ['Default', 'Workflow Two']
    assert 'Workflow One' not in resp.text  # this workflow doesn't have any status
    resp.forms[0]['workflow_id'] = workflow.id
    resp = resp.forms[0].submit('submit')
    assert FormDef.get(formdef.id).workflow_id == str(workflow.id)

    # run a SQL SELECT and we known all columns are defined.
    FormDef.get(formdef.id).data_class().select()

    Category.wipe()
    WorkflowCategory.wipe()
    cat1 = WorkflowCategory(name='Foo')
    cat1.store()
    cat2 = WorkflowCategory(name='Bar')
    cat2.store()

    wf = Workflow(name='Workflow Foo zz')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    wf.category = cat1
    wf.store()
    wf = Workflow(name='Workflow Foo aa')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    wf.category = cat1
    wf.store()
    wf = Workflow(name='Workflow Bar bb')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    wf.category = cat2
    wf.store()
    wf = Workflow(name='Workflow Bar (bb)')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    wf.category = cat2
    wf.store()
    resp = app.get('/backoffice/forms/1/workflow')
    assert [x[2] for x in resp.form['workflow_id'].options] == [
        'Default',
        'Workflow Bar bb',
        'Workflow Bar (bb)',
        'Workflow Foo aa',
        'Workflow Foo zz',
        'Workflow Two',
    ]

    cat = Category(name='Foo')
    cat.store()
    formdef.category = cat
    formdef.store()
    resp = app.get('/backoffice/forms/1/workflow')
    assert [x[2] for x in resp.form['workflow_id'].options] == [
        'Default',
        'Workflow Foo aa',
        'Workflow Foo zz',
        'Workflow Bar bb',
        'Workflow Bar (bb)',
        'Workflow Two',
    ]

    workflow.category = cat1
    workflow.store()
    resp = app.get('/backoffice/forms/1/workflow')
    assert [x[2] for x in resp.form['workflow_id'].options] == [
        'Default',
        'Workflow Foo aa',
        'Workflow Foo zz',
        'Workflow Two',
        'Workflow Bar bb',
        'Workflow Bar (bb)',
    ]


def test_form_workflow_link(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert '/backoffice/workflows/_default/' in resp.text

    formdef.workflow_id = 42
    formdef.store()
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert '/backoffice/workflows/_unknown/' not in resp.text

    formdef.workflow = workflow
    formdef.store()

    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert '/backoffice/workflows/%s/' % workflow.id in resp.text

    # check workflow link is not displayed if user has no access right
    pub.cfg['admin-permissions'] = {'workflows': ['x']}  # block access
    pub.write_cfg()
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert '/backoffice/workflows/%s/' % workflow.id not in resp.text


def test_form_workflow_remapping(pub):
    AfterJob.wipe()
    user = create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata1 = data_class()
    formdata1.status = 'wf-new'
    formdata1.store()

    formdata2 = data_class()
    formdata2.status = 'draft'
    formdata2.store()

    formdata3 = data_class()
    formdata3.status = 'wf-1'
    formdata3.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()
    workflow = Workflow(name='Workflow Two')
    # create it with a single status
    workflow.possible_status = [Workflow.get_default_workflow().possible_status[-1]]
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo3-3x', label='bo field'),
    ]
    workflow.store()

    afterjob_criterias = [Equal('class_name', 'WorkflowChangeJob')]

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = workflow.id
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/workflow-status-remapping?new=2'
    resp = resp.follow()
    assert resp.pyquery('.SingleSelectWidget').length == 5
    resp = resp.forms[0].submit()
    assert resp.pyquery('.SingleSelectWidget.widget-with-error').length == 4
    for status in Workflow.get_default_workflow().possible_status:
        assert resp.forms[0]['mapping-%s' % status.id]
        assert resp.forms[0]['mapping-%s' % status.id].options[0][0] == ''  # empty option
        # there's only one possible new status
        assert len([x for x in resp.forms[0]['mapping-%s' % status.id].options if x[0]]) == 1
        if not resp.forms[0]['mapping-%s' % status.id].value:
            # set to first status
            resp.forms[0]['mapping-%s' % status.id] = resp.forms[0]['mapping-%s' % status.id].options[1][0]
    assert data_class.get(formdata1.id).status == 'wf-new'
    assert data_class.get(formdata2.id).status == 'draft'
    assert data_class.get(formdata3.id).status == 'wf-1'
    resp = resp.forms[0].submit()
    assert AfterJob.count(afterjob_criterias) == 1
    job = AfterJob.select(afterjob_criterias)[0]
    assert job.status == 'completed'
    resp = resp.follow()  # -> to job processing page
    resp = resp.click('Back')
    assert resp.pyquery('[href="workflow"] .offset').text() == 'Workflow Two'
    AfterJob.wipe()

    # run a SQL SELECT and we known all columns are defined.
    FormDef.get(formdef.id).data_class().select()

    assert data_class.get(formdata1.id).status == 'wf-finished'
    assert data_class.get(formdata2.id).status == 'draft'
    assert data_class.get(formdata3.id).status == 'wf-1-invalid-default'

    # change to another workflow, with no mapping change
    workflow2 = workflow
    workflow = Workflow(name='Workflow Three')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[-2:][:]
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo4', label='another bo field'),
    ]
    workflow.store()

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = workflow.id
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/workflow-status-remapping?new=3'
    resp = resp.follow()
    for status in workflow2.possible_status:
        assert resp.forms[0]['mapping-%s' % status.id].options[0][0] == ''  # empty option
        # there are two status
        assert len([x for x in resp.forms[0]['mapping-%s' % status.id].options if x[0]]) == 2
        if not resp.forms[0]['mapping-%s' % status.id].value:
            # set to first status
            resp.forms[0]['mapping-%s' % status.id] = resp.forms[0]['mapping-%s' % status.id].options[1][0]
    resp = resp.forms[0].submit()
    assert data_class.get(formdata1.id).status == 'wf-finished'
    assert data_class.get(formdata2.id).status == 'draft'
    assert data_class.get(formdata3.id).status == 'wf-1-invalid-default'
    assert AfterJob.count(afterjob_criterias) == 1
    job = AfterJob.select(afterjob_criterias)[0]
    assert job.status == 'completed'
    resp = resp.follow()  # -> to job processing page
    resp = resp.click('Back')
    assert resp.pyquery('[href="workflow"] .offset').text() == 'Workflow Three'
    assert pub.snapshot_class.select_object_history(formdef)[0].comment == 'Workflow change'
    assert pub.snapshot_class.select_object_history(formdef)[0].user_id == str(user.id)

    # run a SQL SELECT and we known all columns are defined.
    FormDef.get(formdef.id).data_class().select()

    # check remapping to an invalid workflow
    resp = app.get('http://example.net/backoffice/forms/1/workflow-status-remapping?new=XXX')
    assert 'Invalid target workflow.' in resp.text

    # fake a job still running
    job.status = 'running'
    job.store()

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = workflow.id
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/workflow-status-remapping?new=3'
    resp = resp.follow()
    assert 'A workflow change is already running.' in resp.text


def test_form_workflow_remapping_from_unknown_workflow(pub):
    AfterJob.wipe()
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata1 = data_class()
    formdata1.status = 'wf-new'
    formdata1.store()

    formdata2 = data_class()
    formdata2.status = 'draft'
    formdata2.store()

    formdata3 = data_class()
    formdata3.status = 'wf-1'
    formdata3.store()

    formdef.workflow_id = 'broken'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = ''  # default workflow
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/workflow-status-remapping?new=_default'
    resp = resp.follow()
    assert 'The current workflow configuration is broken' in resp.text
    assert resp.pyquery('.SingleSelectWidget').length == 1
    resp.forms[0]['mapping'] = 'Rejected'
    resp = resp.forms[0].submit()
    assert AfterJob.count() == 1
    job = AfterJob.select()[0]
    assert job.status == 'completed'
    resp = resp.follow()  # -> to job processing page
    resp = resp.click('Back')
    assert resp.pyquery('[href="workflow"] .offset').text() == 'Default'
    AfterJob.wipe()

    assert data_class.get(formdata1.id).status == 'wf-rejected'
    assert data_class.get(formdata2.id).status == 'draft'
    assert data_class.get(formdata3.id).status == 'wf-rejected'


def test_form_workflow_change_backoffice_fields(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.add_status('st1')
    workflow.store()
    workflow2 = Workflow(name='Workflow Two')
    workflow2.add_status('st1')
    workflow2.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow2)
    workflow2.backoffice_fields_formdef.fields = [fields.StringField(id='bo3', label='bo field')]
    workflow2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata1 = data_class()
    formdata1.just_created()
    formdata1.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = workflow2.id
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/workflow-status-remapping?new=2'
    resp = resp.follow()
    assert 'The workflow removes or changes backoffice fields' not in resp.text

    # add identical backoffice field to first workflow
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow2)
    workflow.backoffice_fields_formdef.fields = [fields.StringField(id='bo3', label='bo field')]
    workflow.store()

    resp = app.get('/backoffice/forms/1/workflow-status-remapping?new=2')
    assert 'The workflow removes or changes backoffice fields' not in resp.text

    # add additional backoffice field to first workflow
    workflow.backoffice_fields_formdef.fields.append(fields.StringField(id='bo4', label='bo2 field'))
    workflow.store()

    resp = app.get('/backoffice/forms/1/workflow-status-remapping?new=2')
    assert 'The workflow removes or changes backoffice fields' in resp.text
    assert resp.pyquery('.warningnotice li').text() == 'bo2 field - Text (line)'

    # add different backoffice field to second workflow
    workflow2.backoffice_fields_formdef.fields.append(fields.StringField(id='bo5', label='bo3 field'))
    workflow2.store()

    resp = app.get('/backoffice/forms/1/workflow-status-remapping?new=2')
    assert 'The workflow removes or changes backoffice fields' in resp.text
    assert resp.pyquery('.warningnotice li').text() == 'bo2 field - Text (line)'


def test_form_workflow_change_no_data(pub):
    AfterJob.wipe()
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.add_status('st1')
    workflow.store()
    workflow2 = Workflow(name='Workflow Two')
    workflow2.add_status('st1')
    workflow2.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow2)
    workflow2.backoffice_fields_formdef.fields = [fields.StringField(id='bo3', label='bo field')]
    workflow2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.status = 'draft'
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='workflow', index=1)
    resp.forms[0]['workflow_id'] = workflow2.id
    resp = resp.forms[0].submit('submit')
    # no remapping page
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    job = AfterJob.select()[0]
    assert job.status == 'completed'


def test_form_submitter_roles(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href=re.compile('^roles$'))
    resp.form['roles$element0'] = 'logged-users'
    assert 'required_authentication_contexts' not in resp.text
    resp = resp.form.submit()
    assert FormDef.get(formdef.id).roles == ['logged-users']

    # add auth contexts support
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'auth-contexts', 'fedict')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href=re.compile('^roles$'))
    assert 'required_authentication_contexts' in resp.text
    resp.form['required_authentication_contexts$element0'].checked = True
    resp = resp.form.submit()
    resp = resp.follow()
    assert FormDef.get(formdef.id).required_authentication_contexts == ['fedict']

    # check internal roles are not advertised
    role2 = pub.role_class(name='internal')
    role2.internal = True
    role2.store()

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href=re.compile('^roles$'))
    assert len(resp.form['roles$element0'].options) == 3  # None, Logged users, foobar
    with pytest.raises(ValueError):
        resp.form['roles$element0'] = str(role2.id)


def test_form_backoffice_roles(pub):
    user = create_superuser(pub)
    role = create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href=re.compile('^backoffice-submission-roles$'))
    with pytest.raises(ValueError):
        resp.form['roles$element0'] = 'logged-users'
    resp.form['roles$element0'] = role.id
    resp = resp.form.submit()
    assert FormDef.get(formdef.id).backoffice_submission_roles == [role.id]

    # check direct link to backoffice submission
    resp = app.get('/backoffice/forms/1/')
    assert formdef.get_backoffice_submission_url() not in resp.text

    user.roles = [role.id]
    user.store()
    resp = app.get('/backoffice/forms/1/')
    assert formdef.get_backoffice_submission_url() in resp.text


def test_form_workflow_role(pub):
    AfterJob.wipe()

    create_superuser(pub)
    role = create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='role/_receiver')
    resp = resp.forms[0].submit('cancel')

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='role/_receiver')
    resp.forms[0]['role_id'] = role.id
    resp = resp.forms[0].submit('submit')
    assert FormDef.get(1).workflow_roles == {'_receiver': '1'}
    assert AfterJob.count() == 2  # reindex + tests
    afterjob = [x for x in AfterJob.select() if x.label == 'Reindexing data after function change'][0]
    assert afterjob.status == 'completed'

    # check it doesn't fail if a second role with the same name exists
    role2 = pub.role_class(name='foobar')
    role2.store()
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='role/_receiver')

    # check HTML is escaped
    role.name = 'foo<strong>bar</strong>'
    role.store()
    resp = app.get('/backoffice/forms/1/')
    assert 'foo<strong>bar</strong>' not in resp.text
    assert 'foo&lt;strong&gt;bar&lt;/strong&gt;' in resp.text


def test_form_workflow_options(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_options = {'2*1*body': 'xxx'}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert '"workflow-options"' not in resp.text


def test_form_workflow_variables(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    # check it's not visible
    assert '"workflow-variables"' not in resp
    # check it doesn't crash anyway
    resp = app.get('/backoffice/forms/1/workflow-variables', status=404)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(fields.StringField(id='1', varname='test', label='Test'))
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert '"workflow-variables"' in resp.text

    # visit the variables page
    resp = resp.click(href='workflow-variables')

    # and set a value
    resp.forms[0]['f1'] = 'foobar'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'

    # check the value has been correctly saved
    assert FormDef.get(formdef.id).workflow_options == {'test': 'foobar'}

    # go back to the variables page, also check value
    resp = resp.follow()
    resp = resp.click(href='workflow-variables')
    assert resp.forms[0]['f1'].value == 'foobar'
    resp.forms[0]['f1'] = 'barbaz'
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/forms/1/'

    # check with a date field
    workflow.variables_formdef.fields.append(fields.DateField(id='2', varname='test2', label='Test2'))
    workflow.store()

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow-variables')
    resp.form['f2'] = '2016-06-17'
    resp = resp.form.submit()
    assert time.strftime('%d %m %y', FormDef.get(formdef.id).workflow_options.get('test2')) == '17 06 16'

    # check with a field with a default value
    workflow.variables_formdef.fields.append(
        fields.StringField(id='3', varname='test3', label='Test3', default_value='123')
    )
    workflow.variables_formdef.fields.append(
        fields.StringField(id='4', varname='test4', label='Test4', hint='Existing hint.', default_value='123')
    )
    workflow.store()
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow-variables')
    assert resp.pyquery('#form_hint_f3').text() == 'Default value: 123'
    assert resp.pyquery('#form_hint_f4').text() == 'Existing hint. Default value: 123'


def test_form_workflow_table_variables(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        fields.TableRowsField(id='1', varname='test', label='Test2', columns=['a'])
    )
    workflow.variables_formdef.fields.append(fields.StringField(id='2', varname='test2', label='Test'))
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert '"workflow-variables"' in resp.text

    # visit the variables page
    resp = resp.click(href='workflow-variables')

    # and set a value
    resp.form['f1$element0$col0'] = 'foobar'
    resp.form['f2'] = 'foobar'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/'

    # check the value has been correctly saved
    assert FormDef.get(formdef.id).workflow_options == {'test': [['foobar']], 'test2': 'foobar'}

    # go back to the variables page, also check value
    resp = resp.follow()
    resp = resp.click(href='workflow-variables')
    assert resp.form['f1$element0$col0'].value == 'foobar'
    assert resp.form['f2'].value == 'foobar'


def test_form_workflow_file_variable(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [fields.FileField(id='1', varname='test', label='Test')]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/workflow-variables')
    resp.forms[0]['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('submit')

    formdef.refresh_from_storage()
    assert formdef.workflow_options['test'].get_content() == b'foobar'

    # simulate ajax upload
    resp = app.get('/backoffice/forms/1/workflow-variables')
    resp.forms[0]['f1$file'] = Upload('test.txt', b'barfoo', 'text/plain')
    upload_url = resp.form['f1$file'].attrs['data-url']
    upload_resp = app.post(upload_url, params=resp.form.submit_fields())
    resp.form['f1$file'] = None
    resp.form['f1$token'] = upload_resp.json[0]['token']
    resp = resp.forms[0].submit('submit')

    formdef.refresh_from_storage()
    assert formdef.workflow_options['test'].get_content() == b'barfoo'

    # check file can be downloaded
    resp = app.get('/backoffice/forms/1/workflow-variables')
    assert resp.click('test.txt').body == b'barfoo'


def test_form_workflow_invalid_file_variable(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [fields.StringField(id='1', varname='test', label='Test')]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/workflow-variables')
    resp.forms[0]['f1'] = 'foobar'
    resp = resp.forms[0].submit()

    # check the value has been correctly saved
    assert FormDef.get(formdef.id).workflow_options == {'test': 'foobar'}

    # modify option type
    workflow.variables_formdef.fields = [fields.FileField(id='1', varname='test', label='Test')]
    workflow.store()

    # do not crash when getting back
    resp = app.get('/backoffice/forms/1/workflow-variables')


def test_form_roles(pub):
    create_superuser(pub)
    role = create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click('User Roles')
    resp = resp.forms[0].submit('cancel')

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('User Roles')
    resp.forms[0]['roles$element0'].value = role.id
    resp = resp.forms[0].submit('submit')
    assert FormDef.get(1).roles == [role.id]


def test_form_always_advertise(pub):
    create_superuser(pub)
    role = create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    # Display to unlogged users
    formdef.roles = [role.id]
    formdef.store()
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('User Roles')
    assert resp.forms[0]['always_advertise'].checked is False
    resp.forms[0]['always_advertise'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert FormDef.get(1).always_advertise is True


def test_form_templates(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', varname='test', label='Test')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': 'hello'}
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    assert_option_display(resp, 'Templates', 'None')
    assert resp.pyquery('[href="options/templates"]').attr.rel == ''  # no popup
    resp = resp.click('Templates')
    assert 'id_template' not in resp.form.fields
    resp.form['digest_template'] = 'X{{form_var_test}}Y'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Templates', 'Custom')
    formdef = FormDef.get(formdef.id)
    assert formdef.digest_templates['default'] == 'X{{form_var_test}}Y'
    assert formdef.lateral_template is None
    assert formdef.submission_lateral_template is None

    assert 'Existing forms will be updated in the background.' in resp.text
    # afterjobs are actually run synchronously during tests; we don't have
    # to wait to check the digest has been updated:
    assert formdef.data_class().get(formdata.id).digests['default'] == 'XhelloY'

    resp = app.get('/backoffice/forms/1/options/templates')
    resp.form['lateral_template'] = 'X{{form_var_test}}Y'
    resp.form['submission_lateral_template'] = 'X{{form_var_test}}YZ'
    resp = resp.form.submit().follow()
    assert_option_display(resp, 'Templates', 'Custom')
    formdef = FormDef.get(formdef.id)
    assert formdef.digest_templates['default'] == 'X{{form_var_test}}Y'
    assert formdef.lateral_template == 'X{{form_var_test}}Y'
    assert formdef.submission_lateral_template == 'X{{form_var_test}}YZ'
    assert 'Existing forms will be updated in the background.' not in resp.text


def test_form_delete(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdef2 = FormDef()
    formdef2.name = 'form title'
    formdef2.fields = []
    formdef2.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'foo'
    custom_view.formdef = formdef
    custom_view.store()
    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'foo'
    custom_view2.formdef = formdef2
    custom_view2.store()
    custom_view3 = pub.custom_view_class()
    custom_view3.title = 'foo'
    custom_view3.formdef = carddef
    custom_view3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='delete')
    snapshot_before_delete = pub.snapshot_class.get_latest('formdef', formdef.id)
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/'
    resp = resp.follow()
    snapshot_after_delete = pub.snapshot_class.get_latest('formdef', formdef.id, include_deleted=True)
    assert snapshot_after_delete.id != snapshot_before_delete.id
    assert snapshot_after_delete.comment == 'Deletion'
    assert FormDef.count() == 1
    assert FormDef.select()[0].id == formdef2.id
    assert pub.custom_view_class.count() == 2
    assert pub.custom_view_class.get(custom_view2.id)
    assert pub.custom_view_class.get(custom_view3.id)


def test_form_delete_with_data(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))

    # check with an active formdata (deletion not allowed)
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='delete')
    assert 'Deletion is not possible' in resp.text
    assert 'Beware submitted forms will also be deleted.' not in resp.text

    # check with an existing draft (deletion allowed)
    formdata.status = 'draft'
    formdata.store()
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='delete')
    assert 'Deletion is not possible' not in resp.text
    assert 'Beware submitted forms will also be deleted.' not in resp.text

    # check with a rejected formdata (deletion allowed but warning displayed)
    formdata.status = 'wf-rejected'
    formdata.store()
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='delete')
    assert 'Deletion is not possible' not in resp.text
    assert 'Beware submitted forms will also be deleted.' in resp.text


def test_form_delete_with_tests(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    app = login(get_app(pub))

    # generate test results
    app.get('/backoffice/forms/1/tests/results/run').follow()
    assert TestResults.count() == 1

    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='delete')
    resp.form.submit().follow()

    assert FormDef.count() == 0
    assert TestDef.count() == 0
    assert WorkflowTests.count() == 0
    assert TestResults.count() == 0


def test_form_duplicate(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'form title (copy)'
    resp = resp.form.submit('cancel').follow()
    assert FormDef.count() == 1

    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'form title (copy)'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/2/'
    resp = resp.follow()
    assert FormDef.count() == 2
    assert FormDef.get(2).name == 'form title (copy)'

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'form title (copy 2)'
    resp.form['name'].value = 'other copy'
    resp = resp.form.submit('submit').follow()
    assert FormDef.count() == 3
    assert FormDef.get(3).name == 'other copy'


def test_form_duplicate_with_tests(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    TestDef.wipe()
    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='xxx'),
    ]
    testdef.name = 'First test'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'First response'
    response.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(id='1', status_name='yyy'),
    ]
    testdef.name = 'Second test'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Second response'
    response.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click(href='duplicate')
    resp = resp.form.submit('submit').follow()
    assert FormDef.count() == 2
    assert TestDef.count() == 4
    assert WorkflowTests.count() == 4
    assert WebserviceResponse.count() == 4

    new_formdef = FormDef.get(2)
    assert new_formdef.name == 'form title (copy)'

    testdef1, testdef2 = TestDef.select_for_objectdef(new_formdef)
    assert testdef1.name == 'First test'
    assert testdef2.name == 'Second test'

    assert testdef1.workflow_tests.actions[0].button_name == 'xxx'
    assert testdef2.workflow_tests.actions[0].status_name == 'yyy'
    assert testdef1.get_webservice_responses()[0].name == 'First response'
    assert testdef2.get_webservice_responses()[0].name == 'Second response'


def test_form_export(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click(href='export')
    xml_export = resp.text
    assert ET.fromstring(xml_export).attrib['url'] == 'http://example.net/backoffice/forms/1/'

    fd = io.StringIO(xml_export)
    formdef2 = FormDef.import_from_xml(fd)
    assert formdef2.name == 'form title'


def test_form_import(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    FormDef.wipe()
    assert FormDef.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()
    assert FormDef.count() == 1

    # import the same formdef a second time, make sure url name and internal
    # identifier are not reused
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit().follow()
    assert 'This form has been successfully imported.' in resp.text
    assert 'The form identifier (form-title) was already used by another form.' in resp.text
    assert 'A new one has been generated (form-title-1).' in resp.text
    assert FormDef.count() == 2
    assert FormDef.get(1).url_name == 'form-title'
    assert FormDef.get(2).url_name == 'form-title-1'
    assert FormDef.get(1).table_name == 'formdata_1_form_title'
    assert FormDef.get(2).table_name == 'formdata_2_form_title_1'

    # import a formdef with an url name that doesn't match its title,
    # it should be kept intact.
    formdef.url_name = 'xxx-other-form-title'
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()
    assert FormDef.get(3).url_name == 'xxx-other-form-title'
    assert FormDef.get(3).table_name == 'formdata_3_xxx_other_form_title'

    # import an invalid file
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('formdef.wcs', b'garbage')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text

    # xml with duplicate id, fix it
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='42', label='1st field'),
        fields.StringField(id='42', label='2nd field'),
    ]
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))
    FormDef.wipe()
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.form.submit()
    resp = resp.follow()
    assert 'form contained errors and has been automatically fixed' in resp.text
    assert FormDef.count() == 1
    assert FormDef.get(1).fields[0].id == '1'
    assert FormDef.get(1).fields[1].id == '2'


def test_form_import_from_url(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    FormDef.wipe()
    assert FormDef.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp = resp.form.submit()
    assert 'You have to enter a file or a URL' in resp

    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.invalid/test.wcs', body=ConnectionError('...'))
        resp.form['url'] = 'http://remote.invalid/test.wcs'
        resp = resp.form.submit()
        assert 'Error loading form' in resp
        rsps.get('http://remote.invalid/test.wcs', body=formdef_xml.decode())
        resp.form['url'] = 'http://remote.invalid/test.wcs'
        resp = resp.form.submit()

    assert FormDef.count() == 1
    formdef = FormDef.get(1)
    assert formdef.import_source_url == 'http://remote.invalid/test.wcs'


def test_form_import_with_tests(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    TestDef.wipe()
    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='xxx'),
    ]
    testdef.name = 'First test'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'First response'
    response.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(id='1', status_name='yyy'),
    ]
    testdef.name = 'Second test'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Second response'
    response.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/')
    export_resp = resp.click(href='export')

    FormDef.wipe()
    TestDef.wipe()
    WebserviceResponse.wipe()

    resp = app.get('/backoffice/forms/import')
    resp.forms[0]['file'] = Upload('formdef.wcs', export_resp.body)
    resp = resp.forms[0].submit()

    assert FormDef.count() == 1
    formdef = FormDef.get(1)
    assert not hasattr(formdef, 'xml_testdefs')

    testdef1, testdef2 = TestDef.select_for_objectdef(formdef)
    assert testdef1.name == 'First test'
    assert testdef2.name == 'Second test'

    # import the same formdef a second time
    resp = app.get('/backoffice/forms/import')
    resp.forms[0]['file'] = Upload('formdef.wcs', export_resp.body)
    resp = resp.forms[0].submit()

    assert FormDef.count() == 2
    assert TestDef.count() == 4
    formdef2 = formdef.get(2)

    testdef1, testdef2 = TestDef.select_for_objectdef(formdef2)
    assert testdef1.name == 'First test'
    assert testdef2.name == 'Second test'

    assert testdef1.workflow_tests.actions[0].button_name == 'xxx'
    assert testdef2.workflow_tests.actions[0].status_name == 'yyy'
    assert testdef1.get_webservice_responses()[0].name == 'First response'
    assert testdef2.get_webservice_responses()[0].name == 'Second response'

    TestDef.remove_object(testdef1.id)
    assert TestDef.count() == 3

    # overwrite replaces tests
    resp = app.get('/backoffice/forms/2/')
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', export_resp.body)
    resp = resp.forms[0].submit()

    assert TestDef.count() == 4

    # edit test, then form, ensure changes are persisted
    testdef = TestDef.select_for_objectdef(formdef2)[0]
    testdef.name = 'Modified'
    testdef.store()

    resp = app.get('/backoffice/forms/2/')
    resp = resp.click('change title')
    resp.form['name'] = 'new title'
    resp = resp.form.submit().follow()

    testdef = TestDef.get(testdef.id)
    assert testdef.name == 'Modified'


def test_form_qrcode(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click(href='qrcode')
    assert '<div id="qrcode">' in resp.text
    resp = resp.click('Download')
    assert resp.content_type == 'image/png'


def test_form_description(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Description', 'None')

    resp = resp.click('Description')
    assert resp.pyquery('[data-widget-name="description"]').hasClass('MiniRichTextWidget')
    resp.forms[0]['description'].value = '<p>Hello &amp; World</p>'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/'
    resp = resp.follow()
    assert_option_display(resp, 'Description', 'Hello & World')

    resp = app.get('/backoffice/forms/1/options/description')
    assert resp.pyquery('[data-widget-name="description"]').hasClass('MiniRichTextWidget')

    formdef.description = '<ul><li>test</li></ul>'  # not supported by mini godo
    formdef.store()
    resp = app.get('/backoffice/forms/1/options/description')
    assert resp.pyquery('[data-widget-name="description"]').hasClass('RichTextWidget')

    formdef.description = '<table><tr><td>test</td></tr>'  # not supported by godo
    formdef.store()
    resp = app.get('/backoffice/forms/1/options/description')
    assert resp.pyquery('[data-widget-name="description"]').hasClass('WysiwygTextWidget')


def test_form_keywords(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert_option_display(resp, 'Keywords', 'None')
    resp = resp.click('Keywords')
    resp.forms[0]['keywords'] = 'foo, bar'
    resp = resp.forms[0].submit().follow()
    assert_option_display(resp, 'Keywords', 'foo, bar')
    formdef.refresh_from_storage()
    assert formdef.keywords_list == ['foo', 'bar']


def test_form_enable_from_fields_page(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.disabled = True
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert 'This form is currently disabled.' in resp
    resp = resp.click('Enable').follow()
    assert resp.request.path == '/backoffice/forms/1/fields/'
    assert 'This form is currently disabled.' not in resp


def test_form_disabled_redirection_info_on_fields_page(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.disabled = True
    formdef.disabled_redirection = 'http://example.net'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    assert 'This form is currently disabled' in resp.text
    assert resp.pyquery('.warningnotice a').attr.href == 'http://example.net'


def test_form_new_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert 'There are not yet any fields' in resp.text

    resp = resp.forms[0].submit().follow()
    assert 'Submitted form was not filled properly.' in resp.text

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    resp = resp.follow()
    assert 'foobar' in resp.text
    assert 'Use drag and drop' in resp.text

    assert len(FormDef.get(1).fields) == 1
    assert FormDef.get(1).fields[0].key == 'string'
    assert FormDef.get(1).fields[0].label == 'foobar'
    assert FormDef.get(1).fields[0].varname == 'foobar'

    # add a title too
    resp.forms[0]['label'] = 'baz'
    resp.forms[0]['type'] = 'title'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    resp = resp.follow()

    assert len(FormDef.get(1).fields) == 2
    assert FormDef.get(1).fields[1].key == 'title'
    assert FormDef.get(1).fields[1].label == 'baz'
    assert not FormDef.get(1).fields[1].varname

    # check it's in the preview
    resp = app.get('/backoffice/forms/1/')
    assert resp.pyquery('.form-preview h3').text() == 'baz'


def test_form_new_field_auto_varname(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))

    for label, expected_varname in (
        ('foobar', 'foobar'),
        ('  foobar', 'foobar'),
        ('__foobar', 'foobar'),
        ('--foobar', 'foobar'),
        ('00foobar', 'foobar'),
        ('_0foobar', 'foobar'),
        ('foo bar', 'foo_bar'),
        ('f_oo bar ', 'f_oo_bar'),
    ):
        formdef.fields = []
        formdef.store()
        resp = app.get('/backoffice/forms/1/')
        resp = resp.click(href='fields/')
        resp.forms[0]['label'] = label
        resp.forms[0]['type'] = 'string'
        resp = resp.forms[0].submit().follow()
        assert len(FormDef.get(1).fields) == 1
        assert FormDef.get(1).fields[0].key == 'string'
        assert FormDef.get(1).fields[0].varname == expected_varname


def test_form_preview_map_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.MapField(id='1', label='a field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert 'qommon.map.js' in resp.text
    assert resp.pyquery('#form_f1.qommon-map')


def test_form_preview_do_not_log_error(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.CommentField(id='1', label='<p>{{ "test"|objects:"xxx" }}</p>')]
    formdef.store()

    app = login(get_app(pub))
    LoggedError.wipe()
    app.get('/backoffice/forms/1/')
    assert LoggedError.count() == 0  # error not recorded


def test_form_preview_cut_off_data_sources(pub):
    create_superuser(pub)
    create_role(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [fields.StringField(id='0', label='text', varname='text')]
    carddef.digest_templates = {'default': 'x{{form_var_text}}y'}
    carddef.store()

    for i in range(200):
        carddata = carddef.data_class()()
        carddata.data = {'0': f'x{i}'}
        carddata.just_created()
        carddata.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='bar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': str(x), 'text': f't{x}'} for x in range(300)]),
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='1', label='t1', data_source={'type': 'carddef:foo'}),
        fields.ItemField(id='2', label='t2', data_source={'type': 'bar'}),
    ]
    formdef.store()

    app = login(get_app(pub))
    LoggedError.wipe()
    resp = app.get(formdef.get_admin_url())
    assert resp.pyquery('#form_f1 option').length == 100
    assert resp.pyquery('#form_f2 option').length == 100
    assert 'Warning: this field has too many choices, it will slow down the display.' in resp.text


def test_form_field_without_label(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.CommentField(id='1', label=None)]
    formdef.store()

    app = login(get_app(pub))
    app.get('/backoffice/forms/1/fields/', status=200)  # ok, no error


def test_form_field_required_info(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='test', required='required')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    assert not resp.pyquery('#fieldId_1 .optional').text()

    formdef.fields[0].required = 'optional'
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/')
    assert resp.pyquery('#fieldId_1 .optional').text() == '- optional'

    formdef.fields[0].required = 'frontoffice'
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/')
    assert resp.pyquery('#fieldId_1 .optional').text() == '- required only in frontoffice'


def test_form_field_varname_values(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')

    resp.forms[0]['varname'] = 'id'
    resp = resp.forms[0].submit('submit')
    assert 'this value is reserved for internal use.' in resp.text

    resp.forms[0]['varname'] = '0123'
    resp = resp.forms[0].submit('submit')
    assert 'must only consist of letters, numbers, or underscore' in resp.text

    resp.forms[0]['varname'] = 'plop'
    resp = resp.forms[0].submit('submit')
    formdef.refresh_from_storage()
    assert formdef.fields[0].varname == 'plop'


def test_form_delete_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text
    assert 'Use drag and drop' in resp.text
    assert 'Also remove all fields from the page' not in resp.text

    resp = resp.click(href='1/delete')
    assert 'You are about to remove the "1st field" field.' in resp.text
    assert 'Warning:' not in resp.text
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 0


def test_form_delete_field_existing_data(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.CommentField(id='2', label='comment field'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': 'hello'}
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    resp = resp.click(href='1/delete')
    assert 'You are about to remove the "1st field" field.' in resp.text
    assert 'Warning: this field data will be permanently deleted from existing forms.' in resp.text
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_2'
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 1

    # check non-data fields do not show this warning
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    resp = resp.click(href='2/delete')
    assert 'You are about to remove the "comment field" field.' in resp.text
    assert 'Warning:' not in resp.text
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 0


def test_form_delete_page_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='page 1'),
        fields.StringField(id='2', label='field 1 1'),
        fields.StringField(id='3', label='field 1 2'),
        fields.PageField(id='4', label='page 2'),
        fields.PageField(id='5', label='page 3'),
        fields.StringField(id='6', label='field 3 1'),
        fields.StringField(id='7', label='field 3 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))

    # delete fields from the page
    resp = app.get('/backoffice/forms/1/fields/1/delete')
    assert 'You are about to remove the "page 1" page.' in resp.text
    assert 'Also remove all fields from the page' in resp.text
    resp.forms[0]['delete_fields'] = True
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 4

    # empty page
    resp = app.get('/backoffice/forms/1/fields/4/delete')
    assert 'You are about to remove the "page 2" page.' in resp.text
    assert 'Also remove all fields from the page' not in resp.text
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 3

    # keep fields
    resp = app.get('/backoffice/forms/1/fields/5/delete')
    assert 'You are about to remove the "page 3" page.' in resp.text
    assert 'Also remove all fields from the page' in resp.text
    resp.forms[0]['delete_fields'] = False
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 2


def test_form_duplicate_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    assert '1st field' in resp.text

    resp = resp.click(href='1/duplicate')
    formdef.refresh_from_storage()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_%s' % formdef.fields[1].id
    resp = resp.follow()
    assert len(FormDef.get(1).fields) == 2
    assert FormDef.get(1).fields[0].label == '1st field'
    assert FormDef.get(1).fields[1].label == '1st field'


def test_form_duplicate_page_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='1st field', varname='foobar'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='2nd field', varname='baz'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')

    # duplicate 1st page only
    resp = resp.click(href='0/duplicate')
    assert 'Also duplicate all fields of the page' in resp.text
    resp = resp.form.submit().follow()
    assert [f.label for f in FormDef.get(1).fields] == [
        '1st page',
        '1st field',
        '1st page',
        '2nd page',
        '2nd field',
    ]
    formdef.refresh_from_storage()
    field4 = formdef.fields[2]
    assert [str(f.id) for f in FormDef.get(1).fields] == ['0', '1', field4.id, '2', '3']

    # duplicate 1st page and fields
    resp = resp.click(href='^0/duplicate')
    assert 'Also duplicate all fields of the page' in resp.text
    resp.form['duplicate_fields'] = True
    resp = resp.form.submit().follow()
    assert [f.label for f in FormDef.get(1).fields] == [
        '1st page',
        '1st field',
        '1st page',
        '1st field',
        '1st page',
        '2nd page',
        '2nd field',
    ]
    formdef.refresh_from_storage()
    field5 = formdef.fields[2]
    field6 = formdef.fields[3]
    assert [str(f.id) for f in FormDef.get(1).fields] == [
        '0',
        '1',
        field5.id,
        field6.id,
        field4.id,
        '2',
        '3',
    ]

    # duplicate copy of 1st page without fields
    resp = resp.click(href='%s/duplicate' % field4.id)
    assert 'Also duplicate all fields of the page' not in resp.text
    resp = resp.form.submit().follow()
    assert [f.label for f in FormDef.get(1).fields] == [
        '1st page',
        '1st field',
        '1st page',
        '1st field',
        '1st page',
        '1st page',
        '2nd page',
        '2nd field',
    ]
    formdef.refresh_from_storage()
    field7 = formdef.fields[5]
    assert [str(f.id) for f in FormDef.get(1).fields] == [
        '0',
        '1',
        field5.id,
        field6.id,
        field4.id,
        field7.id,
        '2',
        '3',
    ]

    # duplicate last page and fields
    resp = resp.click(href='^2/duplicate')
    assert 'Also duplicate all fields of the page' in resp.text
    resp.form['duplicate_fields'] = True
    resp = resp.form.submit().follow()
    assert [f.label for f in FormDef.get(1).fields] == [
        '1st page',
        '1st field',
        '1st page',
        '1st field',
        '1st page',
        '1st page',
        '2nd page',
        '2nd field',
        '2nd page',
        '2nd field',
    ]
    formdef.refresh_from_storage()
    field8 = formdef.fields[8]
    field9 = formdef.fields[9]
    assert [str(f.id) for f in FormDef.get(1).fields] == [
        '0',
        '1',
        field5.id,
        field6.id,
        field4.id,
        field7.id,
        '2',
        '3',
        field8.id,
        field9.id,
    ]

    # duplicate last page only
    resp = resp.click(href='%s/duplicate' % field8.id)
    assert 'Also duplicate all fields of the page' in resp.text
    resp = resp.form.submit().follow()
    assert [f.label for f in FormDef.get(1).fields] == [
        '1st page',
        '1st field',
        '1st page',
        '1st field',
        '1st page',
        '1st page',
        '2nd page',
        '2nd field',
        '2nd page',
        '2nd field',
        '2nd page',
    ]
    formdef.refresh_from_storage()
    field10 = formdef.fields[10]
    assert [str(f.id) for f in FormDef.get(1).fields] == [
        '0',
        '1',
        field5.id,
        field6.id,
        field4.id,
        field7.id,
        '2',
        '3',
        field8.id,
        field9.id,
        field10.id,
    ]


def test_form_duplicate_file_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')

    # add a first field
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'file'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/%s/fields/' % formdef.id
    resp = resp.follow()
    assert 'foobar' in resp.text

    resp = resp.click(href='%s/duplicate' % FormDef.get(formdef.id).fields[0].id)
    formdef.refresh_from_storage()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_%s' % formdef.fields[1].id
    resp = resp.follow()


def test_form_edit_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = resp.click(href=r'^1/$')
    assert '/backoffice/forms/1/fields/#fieldId_1' in resp
    assert resp.pyquery('.field-edit--title').text() == '1st field'
    assert resp.pyquery('.field-edit--subtitle').text() == 'Text (line)'
    assert resp.forms[0]['label'].value == '1st field'
    resp.forms[0]['label'] = 'changed field'
    resp.forms[0]['required'] = 'optional'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'

    assert FormDef.get(1).fields[0].label == 'changed field'
    assert FormDef.get(1).fields[0].required == 'optional'


def test_form_edit_field_anonymisation(pub):
    create_superuser(pub)
    create_role(pub)

    for field_class in fields.base.field_classes:
        if field_class.is_no_data_field or field_class.key == 'computed':
            continue
        FormDef.wipe()
        formdef = FormDef()
        formdef.name = 'form title'
        formdef.fields = [field_class(id='1', label='1st field')]
        formdef.store()

        app = login(get_app(pub))
        resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
        assert resp.form['anonymise'].value == field_class.anonymise
        resp.form['anonymise'] = 'no'
        for widget_name in ('rows$element0', 'columns$element0', 'items$element0'):
            # some combination of those parameters are required for table fields
            if widget_name in resp.form.fields.keys():
                resp.form[widget_name] = 'a'
        resp = resp.form.submit('submit')
        formdef.refresh_from_storage()
        assert formdef.fields[0].anonymise == 'no'


def test_form_edit_field_required(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(f'{formdef.get_admin_url()}fields/1/')
    assert [x[0] for x in resp.form['required'].options] == ['required', 'optional', 'frontoffice']
    resp.form['required'] = 'frontoffice'
    resp = resp.form.submit('submit')
    formdef.refresh_from_storage()
    assert formdef.fields[0].required == 'frontoffice'

    resp = app.get(f'{formdef.get_admin_url()}inspect')
    assert resp.pyquery('.parameter-required').text() == 'Required: Only in frontoffice'


def test_form_edit_field_advanced(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == '1st field'
    assert '>Prefill</label>' in resp.text
    # check the "prefill" field is in advanced panel and there's no visual marker
    assert resp.pyquery('#panel-advanced .PrefillSelectionWidget')
    assert not resp.pyquery('#tab-advanced.pk-tabs--button-marker')

    # complete the "prefill" field
    resp.forms[0]['prefill$type'] = 'String / Template'
    resp.forms[0]['prefill$value_string'] = 'test'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'
    resp = resp.follow()

    assert FormDef.get(formdef.id).fields[0].prefill == {
        'type': 'string',
        'value': 'test',
        'locked': False,
        'locked-unless-empty': False,
    }

    # do the same with 'data sources' field
    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == '1st field'
    assert '>Data Source</label>' in resp.text
    # check the "data source" field is in advanced panel
    assert resp.pyquery('#panel-advanced .DataSourceSelectionWidget')

    # start filling the "data source" field
    resp.forms[0]['data_source$type'] = 'json'
    resp.forms[0]['data_source$value'] = 'http://example.net'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    # it is still in the advanced panel, with a visual marker
    resp = resp.click(href=r'^1/$')
    assert resp.pyquery('#panel-advanced .DataSourceSelectionWidget')
    assert resp.pyquery('#tab-advanced.pk-tabs--button-marker')

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.forms[0]['label'].value == '1st field'
    resp.forms[0]['prefill$type'] = 'User Field'
    resp.forms[0]['prefill$value_user'] = 'Email (builtin)'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    resp = resp.follow()
    assert (
        "&quot;1st field&quot; is not an email field. Are you sure you want to prefill it with user's email?"
        in resp.text
    )

    formdef.fields += [fields.EmailField(id='2', label='2nd field')]
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/2/')
    assert resp.forms[0]['label'].value == '2nd field'
    resp.forms[0]['prefill$type'] = 'User Field'
    resp.forms[0]['prefill$value_string'] = 'email'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_2'
    resp = resp.follow()
    assert 'Are you sure you want to prefill' not in resp.text


def test_form_edit_prefill_text(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.TextField(id='2', label='2nd field'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('input[name="prefill$value_string"]')
    resp.forms[0]['prefill$type'] = 'String / Template'
    resp.forms[0]['prefill$value_string'] = 'xxx'
    resp = resp.forms[0].submit('submit')
    formdef.refresh_from_storage()
    assert formdef.fields[0].prefill == {
        'type': 'string',
        'locked': False,
        'locked-unless-empty': False,
        'value': 'xxx',
    }

    resp = app.get(formdef.get_admin_url() + 'fields/2/')
    assert resp.pyquery('textarea[name="prefill$value_string"]')
    resp.forms[0]['prefill$type'] = 'String / Template'
    resp.forms[0]['prefill$value_string'] = 'yyy'
    resp = resp.forms[0].submit('submit')
    formdef.refresh_from_storage()
    assert formdef.fields[1].prefill == {
        'type': 'string',
        'locked': False,
        'locked-unless-empty': False,
        'value': 'yyy',
    }


def test_form_edit_field_display(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.TitleField(id='1', label='Title'),
        fields.SubtitleField(id='2', label='Subtitle'),
        fields.StringField(id='3', label='1st field'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert not resp.pyquery('#tab-display.pk-tabs--button-marker')
    resp = app.get('/backoffice/forms/1/fields/2/')
    assert not resp.pyquery('#tab-display.pk-tabs--button-marker')
    resp = app.get('/backoffice/forms/1/fields/3/')
    assert not resp.pyquery('#tab-display.pk-tabs--button-marker')


def test_form_prefill_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['prefill$type'] = 'String / Template'
    resp.form['prefill$value_string'] = 'test'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].prefill == {
        'type': 'string',
        'value': 'test',
        'locked': False,
        'locked-unless-empty': False,
    }

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['prefill$type'] = 'String / Template'
    resp.form['prefill$value_string'] = '{{form_var_toto}}'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].prefill == {
        'type': 'string',
        'value': '{{form_var_toto}}',
        'locked': False,
        'locked-unless-empty': False,
    }

    # check error handling
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['prefill$type'] = 'String / Template'
    resp.form['prefill$value_string'] = '{% if %}'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template: Unexpected end of expression' in resp.text


def test_form_prefill_type_options(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.forms[0]['prefill$type'].options == [
        ('None', True, 'None'),
        ('String / Template', False, 'String / Template'),
        ('User Field', False, 'User Field'),
        ('Geolocation', False, 'Geolocation'),
    ]


def test_form_edit_string_field_maxlength(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['maxlength'] = 'blah'
    resp = resp.form.submit('submit')
    assert 'The maximum number of characters must be empty or a number.' in resp.text
    resp.form['maxlength'] = '123'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].maxlength == '123'


def test_form_edit_string_field_validation(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = resp.click(href=r'^1/$')
    resp.form['validation$type'] = 'regex'
    resp.form['validation$value_regex'] = r'\d+'
    resp.form['validation$error_message'] = 'Foo Error'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].validation == {
        'type': 'regex',
        'value': r'\d+',
        'error_message': 'Foo Error',
    }

    resp = resp.click(href=r'^1/$')
    resp.form['validation$type'] = ''
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].validation is None

    resp = resp.click(href='^1/$')
    resp.form['validation$type'] = 'django'
    resp.form['validation$value_django'] = 'value|decimal < 20'
    resp.form['validation$error_message'] = 'Bar Error'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].validation == {
        'type': 'django',
        'value': 'value|decimal < 20',
        'error_message': 'Bar Error',
    }

    resp = resp.click(href=r'^1/$')
    resp.form['validation$type'] = 'django'
    resp.form['validation$value_django'] = '{{ value|decimal < 20 }}'
    resp = resp.form.submit('submit')
    assert 'syntax error' in resp.text

    # check default error message is not saved
    resp.form['validation$value_django'] = ''
    resp.form['validation$type'] = 'time'
    resp.form['validation$error_message'] = 'You should enter a valid time, between 00:00 and 23:59.'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].validation == {
        'type': 'time',
    }

    # but custom message is saved
    resp = resp.click(href=r'^1/$')
    resp.form['validation$type'] = 'time'
    resp.form['validation$error_message'] = 'Invalid time, it must be in hh:mm format.'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].validation == {
        'type': 'time',
        'error_message': 'Invalid time, it must be in hh:mm format.',
    }

    # check disabling of validation types
    resp = resp.click(href=r'^1/$')
    assert 'siren-fr' in [x[0] for x in resp.form['validation$type'].options]
    assert 'iban' in [x[0] for x in resp.form['validation$type'].options]
    assert 'time' in [x[0] for x in resp.form['validation$type'].options]
    resp = resp.form.submit('submit').follow()

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disabled-validation-types', '*-fr, iban')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = resp.click(href=r'^1/$')
    assert 'siren-fr' not in [x[0] for x in resp.form['validation$type'].options]
    assert 'iban' not in [x[0] for x in resp.form['validation$type'].options]
    assert 'time' in [x[0] for x in resp.form['validation$type'].options]
    resp = resp.form.submit('submit').follow()


def test_form_edit_text_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.TextField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['display_mode'].options == [
        ('Rich Text (simple: bold, italic...)', False, None),
        ('Rich Text (full: titles, lists...)', False, None),
        ('Plain Text (with automatic paragraphs on blank lines)', True, None),
        ('Plain Text (with linebreaks as typed)', False, None),
    ]

    resp.form['maxlength'] = 'blah'
    resp = resp.form.submit('submit')
    assert 'The maximum number of characters must be empty or a number.' in resp.text
    resp.form['maxlength'] = '123'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].maxlength == '123'


def test_form_edit_item_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == '1st field'
    resp.forms[0]['label'] = 'changed field'
    resp.forms[0]['required'] = 'optional'
    resp = resp.forms[0].submit('items$add_element')
    # this adds a second field
    assert 'items$element0' in resp.form.fields
    assert 'items$element1' in resp.form.fields
    # but don't fill anything
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'
    resp = resp.follow()

    assert FormDef.get(1).fields[0].label == 'changed field'
    assert FormDef.get(1).fields[0].required == 'optional'
    assert FormDef.get(1).fields[0].items is None

    # edit and fill with one item
    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == 'changed field'
    resp.forms[0]['items$element0'] = 'XXX'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'
    assert FormDef.get(1).fields[0].items == ['XXX']


def test_form_edit_item_field_data_source(pub):
    CardDef.wipe()

    create_superuser(pub)
    create_role(pub)

    NamedDataSource.wipe()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['data_source$type'].options == [
        ('None', True, 'None'),
        ('json', False, 'JSON URL'),
        ('jsonp', False, 'JSONP URL'),
        ('jsonvalue', False, 'JSON Expression'),
    ]
    resp = resp.form.submit('submit').follow()

    data_source = NamedDataSource(name='Foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/404'}
    data_source.record_on_errors = True
    data_source.notify_on_errors = True
    data_source.store()

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['data_source$type'].options == [
        ('None', True, 'None'),
        ('foobar', False, 'Foobar'),
        ('json', False, 'JSON URL'),
        ('jsonp', False, 'JSONP URL'),
        ('jsonvalue', False, 'JSON Expression'),
    ]
    resp.form['data_mode'].value = 'data-source'
    resp.form['data_source$type'].value = 'foobar'
    resp.form.submit('submit').follow()
    resp = app.get('/backoffice/forms/1/')
    assert FormDef.get(formdef.id).fields[0].data_source == {'type': 'foobar'}
    assert LoggedError.count() == 0  # error not recorded

    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.store()

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['data_source$type'].options == [
        ('None', False, 'None'),
        ('foobar', True, 'Foobar'),
        ('json', False, 'JSON URL'),
        ('jsonp', False, 'JSONP URL'),
        ('jsonvalue', False, 'JSON Expression'),
    ]

    carddef.digest_templates = {'default': 'plop'}
    carddef.store()
    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.store()

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['data_source$type'].options == [
        ('None', False, 'None'),
        ('carddef:%s' % carddef.url_name, False, 'Baz'),
        ('carddef:%s:card-view' % carddef.url_name, False, 'Baz - card view'),
        ('foobar', True, 'Foobar'),
        ('json', False, 'JSON URL'),
        ('jsonp', False, 'JSONP URL'),
        ('jsonvalue', False, 'JSON Expression'),
    ]
    assert (
        resp.pyquery('select#form_data_source__type option')[1].attrib['data-goto-url']
        == carddef.get_admin_url()
    )
    assert (
        resp.pyquery('select#form_data_source__type option')[2].attrib['data-goto-url']
        == carddef.get_url() + 'card-view'
    )
    assert (
        resp.pyquery('select#form_data_source__type option')[3].attrib['data-goto-url']
        == data_source.get_admin_url()
    )

    resp.form['data_source$type'].value = 'carddef:%s' % carddef.url_name
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].data_source == {'type': 'carddef:%s' % carddef.url_name}

    # set json source then back to none
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_source$type'].value = 'json'
    resp.form['data_source$value'].value = 'http://whatever'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(formdef.id).fields[0].data_source == {'type': 'json', 'value': 'http://whatever'}

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_source$type'].value = 'None'
    resp = resp.form.submit('submit').follow()
    resp = app.get('/backoffice/forms/1/')

    # change configuration for items
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_mode'].value = 'simple-list'
    resp.form['items$element0'] = 'XXX'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(1).fields[0].data_source is None
    assert FormDef.get(1).fields[0].items == ['XXX']


def test_form_edit_item_field_many_data_sources(pub):
    create_superuser(pub)
    create_role(pub)

    NamedDataSource.wipe()

    CardDef.wipe()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.form['data_source$type'].options) == 4
    assert resp.form['data_source$type'].attrs.get('data-autocomplete') is None

    for i in range(50):
        data_source = NamedDataSource(name='Foobar %s' % i)
        data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/404'}
        data_source.record_on_errors = True
        data_source.notify_on_errors = True
        data_source.store()

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.form['data_source$type'].options) == 54
    assert resp.form['data_source$type'].attrs.get('data-autocomplete') == 'true'


def test_form_edit_item_field_data_source_with_categories(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    CardDef.wipe()
    DataSourceCategory.wipe()
    NamedDataSource.wipe()
    pub.custom_view_class.wipe()

    data_source = NamedDataSource(name='test')
    data_source.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.pyquery('select[name="data_source$type"] optgroup')) == 2
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).attr['label']
        == 'Manually Configured Data Sources'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).find('option').text() == 'test'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).attr['label']
        == 'Generic Data Sources'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).find('option').text()
        == 'JSON URL JSONP URL JSON Expression'
    )
    assert [o[0] for o in resp.form['data_source$type'].options] == [
        'None',
        'test',
        'json',
        'jsonp',
        'jsonvalue',
    ]

    cat_b = DataSourceCategory(name='Cat B')
    cat_b.store()
    data_source = NamedDataSource(name='foo bar')
    data_source.category_id = cat_b.id
    data_source.store()
    data_source = NamedDataSource(name='bar foo')
    data_source.category_id = cat_b.id
    data_source.store()
    cat_a = DataSourceCategory(name='Cat A')
    cat_a.store()
    data_source = NamedDataSource(name='foo baz')
    data_source.category_id = cat_a.id
    data_source.store()

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.pyquery('select[name="data_source$type"] optgroup')) == 4
    assert PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).attr['label'] == 'Cat A'
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).find('option').text()
        == 'foo baz'
    )
    assert PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).attr['label'] == 'Cat B'
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).find('option').text()
        == 'bar foo foo bar'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[2]).attr['label']
        == 'Without category'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[2]).find('option').text() == 'test'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[3]).attr['label']
        == 'Generic Data Sources'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[3]).find('option').text()
        == 'JSON URL JSONP URL JSON Expression'
    )
    assert [o[0] for o in resp.form['data_source$type'].options] == [
        'None',
        'foo_baz',
        'bar_foo',
        'foo_bar',
        'test',
        'json',
        'jsonp',
        'jsonvalue',
    ]


def test_form_edit_item_field_data_source_with_carddef_categories(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    CardDefCategory.wipe()
    CardDef.wipe()
    DataSourceCategory.wipe()
    NamedDataSource.wipe()
    pub.custom_view_class.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.digest_templates = {'default': 'plop'}
    carddef.fields = [fields.FileField(id='1', label='File field')]
    carddef.store()
    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.store()

    carddef2 = CardDef()
    carddef2.name = 'Bar'
    carddef2.digest_templates = {'default': 'plop'}
    carddef2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.pyquery('select[name="data_source$type"] optgroup')) == 2
    assert PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).attr['label'] == 'Cards'
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).find('option').text()
        == 'Bar Baz Baz - card view'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0])
        .find('option')
        .attr['data-has-image']
        == 'false'
    )

    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).attr['label']
        == 'Generic Data Sources'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).find('option').text()
        == 'JSON URL JSONP URL JSON Expression'
    )
    assert [o[0] for o in resp.form['data_source$type'].options] == [
        'None',
        'carddef:bar',
        'carddef:baz',
        'carddef:baz:card-view',
        'json',
        'jsonp',
        'jsonvalue',
    ]

    category = CardDefCategory(name='Foobar')
    category.store()
    carddef.category = category
    carddef.fields[0].varname = 'image'
    carddef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert len(resp.pyquery('select[name="data_source$type"] optgroup')) == 3
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).attr['label'] == 'Cards - Foobar'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0]).find('option').text()
        == 'Baz Baz - card view'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[0])
        .find('option')
        .attr['data-has-image']
        == 'true'
    )

    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).attr['label']
        == 'Cards - Uncategorised'
    )
    assert PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[1]).find('option').text() == 'Bar'
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[2]).attr['label']
        == 'Generic Data Sources'
    )
    assert (
        PyQuery(resp.pyquery('select[name="data_source$type"] optgroup')[2]).find('option').text()
        == 'JSON URL JSONP URL JSON Expression'
    )
    assert [o[0] for o in resp.form['data_source$type'].options] == [
        'None',
        'carddef:baz',
        'carddef:baz:card-view',
        'carddef:bar',
        'json',
        'jsonp',
        'jsonvalue',
    ]


def test_form_edit_item_field_geojson_data_source(pub, http_requests):
    NamedDataSource.wipe()
    create_superuser(pub)
    create_role(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/geojson',
    }
    data_source.id_property = 'id'
    data_source.label_template_property = '{{ text }}'
    data_source.cache_duration = '5'
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['display_mode'] = 'map'
    assert resp.pyquery('option[value=foobar][data-type=geojson]')
    resp.form['data_mode'] = 'data-source'
    resp.form['data_source$type'] = 'foobar'
    resp.form['min_zoom'] = 'Wide area'
    resp.form['max_zoom'] = 'Small road'
    resp = resp.form.submit('submit').follow()
    formdef = FormDef.get(formdef.id)
    assert formdef.fields[0].data_source == {'type': 'foobar'}
    assert formdef.fields[0].min_zoom == '9'

    resp = app.get('/backoffice/forms/1/fields/1/')
    assert resp.form['min_zoom'].value == 'Wide area'


def test_form_edit_item_field_check_display_mode(pub):
    NamedDataSource.wipe()
    FormDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    create_superuser(pub)
    create_role(pub)

    carddef = CardDef()
    carddef.name = 'card title'
    carddef.digest_templates = {'default': 'plop'}
    carddef.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))

    for option_type in ('simple-list', 'cards'):
        if option_type == 'simple-list':
            formdef.fields[0].items = ['a', 'b', 'c']
        else:
            formdef.fields[0].data_source = {'type': 'carddef:card-title'}
        formdef.store()

        resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
        resp.form['display_mode'] = 'map'
        resp = resp.form.submit('submit')
        assert 'Map display is only possible' in resp.text
        resp.form['display_mode'] = 'images'
        resp = resp.form.submit('submit')
        assert 'Image display is only possible' in resp.text
        resp.form['display_mode'] = 'timetable'
        resp = resp.form.submit('submit')
        assert 'Time table display is only possible' in resp.text

    formdef.fields[0].data_source = {'type': 'json', 'value': 'plop'}
    formdef.store()
    resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
    resp.form['display_mode'] = 'timetable'
    resp = resp.form.submit('submit')
    assert 'Time table display is only possible' in resp.text

    formdef.fields[0].data_source = {'type': 'json', 'value': 'plop/datetimes/'}
    formdef.store()
    resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
    resp.form['display_mode'] = 'timetable'
    resp = resp.form.submit('submit')
    assert 'Time table display is only possible' not in resp.text

    formdef.fields[0].data_source = {'type': 'json', 'value': '{{xx}}'}
    formdef.store()
    resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
    resp.form['display_mode'] = 'timetable'
    resp = resp.form.submit('submit')
    assert 'Time table display is only possible' not in resp.text


def test_form_edit_item_field_image_display_mode(pub):
    NamedDataSource.wipe()
    FormDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    create_superuser(pub)
    create_role(pub)

    carddef = CardDef()
    carddef.name = 'Images'
    carddef.fields = [
        fields.StringField(id='0', label='Label', varname='label'),
        fields.FileField(id='1', label='Image', varname='image'),
    ]
    carddef.digest_templates = {'default': '{{form_var_label}}'}
    carddef.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field', data_source={'type': 'carddef:images'})]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
    resp.form['display_mode'] = 'images'
    resp = resp.form.submit('submit').follow()
    formdef.refresh_from_storage()
    assert int(formdef.fields[0].image_desktop_size) == 150

    # allow image size as integer (legacy) and string
    for value in ('150', 150):
        formdef.fields[0].image_desktop_size = value
        formdef.store()
        resp = app.get(f'/backoffice/forms/{formdef.id}/fields/1/')
        resp = resp.form.submit('submit').follow()
        formdef.refresh_from_storage()
        assert int(formdef.fields[0].image_desktop_size) == 150


def test_form_edit_item_field_anonymisation(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert [x[0] for x in resp.form['anonymise'].options] == ['final', 'intermediate', 'no']

    pub.site_options.set('options', 'enable-intermediate-anonymisation', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert [x[0] for x in resp.form['anonymise'].options] == ['final', 'no']


def test_form_edit_items_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemsField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == '1st field'
    assert resp.forms[0]['min_choices'].value == '0'
    assert resp.forms[0]['max_choices'].value == '0'
    resp.forms[0]['label'] = 'changed field'
    resp.forms[0]['required'] = 'optional'
    resp = resp.forms[0].submit('items$add_element')
    # this adds a second field
    assert 'items$element0' in resp.form.fields
    assert 'items$element1' in resp.form.fields
    # but don't fill anything
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'
    resp = resp.follow()

    assert FormDef.get(1).fields[0].label == 'changed field'
    assert FormDef.get(1).fields[0].required == 'optional'
    assert FormDef.get(1).fields[0].items is None
    assert FormDef.get(1).fields[0].min_choices == 0
    assert FormDef.get(1).fields[0].max_choices == 0

    # edit and fill with one item
    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['label'].value == 'changed field'
    resp.forms[0]['items$element0'] = 'XXX'
    resp.forms[0]['min_choices'] = 2
    resp.forms[0]['max_choices'] = 5
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/#fieldId_1'
    assert FormDef.get(1).fields[0].items == ['XXX']
    assert FormDef.get(1).fields[0].min_choices == 2
    assert FormDef.get(1).fields[0].max_choices == 5

    # check prefilling is possible with a template
    resp = resp.follow()
    resp = resp.click(href=r'^1/$')
    assert resp.forms[0]['prefill$type'].options == [
        ('None', True, 'None'),
        ('String / Template', False, 'String / Template'),
    ]

    # change configuration for datasource
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_mode'].value = 'data-source'
    resp.form['data_source$type'].value = 'json'
    resp.form['data_source$value'].value = 'http://whatever'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(1).fields[0].data_source == {'type': 'json', 'value': 'http://whatever'}

    # change configuration for items
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_mode'].value = 'simple-list'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(1).fields[0].data_source is None
    assert FormDef.get(1).fields[0].items == ['XXX']


def test_form_edit_items_datasource(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemsField(id='1', label='1st field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert '1st field' in resp.text

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_mode'].value = 'data-source'
    resp.form['data_source$type'].value = 'json'
    resp.form['data_source$value'].value = 'random string'
    resp = resp.form.submit('submit')
    assert 'Value must be a full URL.' in resp.text
    resp.form['data_source$value'].value = 'http://whatever'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(1).fields[0].data_source == {'type': 'json', 'value': 'http://whatever'}

    # check template strings are ok
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['data_mode'].value = 'data-source'
    resp.form['data_source$type'].value = 'json'
    resp.form['data_source$value'].value = '{{url}}'
    resp = resp.form.submit('submit').follow()
    assert FormDef.get(1).fields[0].data_source == {'type': 'json', 'value': '{{url}}'}


def test_form_edit_page_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')
    assert 'There are not yet any fields' in resp.text

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'page'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    resp = resp.follow()
    assert 'Page #1' in resp.text
    assert 'foobar' in resp.text
    assert 'Use drag and drop' in resp.text
    assert 'with post-conditions' not in resp.text

    formdef.refresh_from_storage()
    assert len(formdef.fields) == 1
    assert formdef.fields[0].key == 'page'
    assert formdef.fields[0].label == 'foobar'

    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)
    resp.form['post_conditions$element0$condition$type'] = 'django'
    resp.form['post_conditions$element0$condition$value_django'] = 'foo'
    resp.form['post_conditions$element0$error_message$value_template'] = 'bar'
    resp = resp.form.submit('post_conditions$add_element')
    # check advanced tab is open after adding a line
    assert resp.pyquery('[aria-selected="true"]').text() == 'Advanced'
    resp.form['post_conditions$element1$condition$type'] = 'django'
    resp.form['post_conditions$element1$condition$value_django'] = 'foo2'
    resp = resp.form.submit('submit')
    assert 'Both condition and error message are required.' in resp.text
    resp.form['post_conditions$element1$error_message$value_template'] = 'bar2'
    resp = resp.form.submit('submit').follow()
    assert 'with post-conditions' in resp.text

    formdef.refresh_from_storage()
    assert formdef.fields[0].post_conditions == [
        {'condition': {'type': 'django', 'value': 'foo'}, 'error_message': 'bar'},
        {'condition': {'type': 'django', 'value': 'foo2'}, 'error_message': 'bar2'},
    ]

    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)
    resp.form['post_conditions$element1$condition$type'] = 'django'
    resp.form['post_conditions$element1$condition$value_django'] = 'foo3'
    resp = resp.form.submit('submit').follow()
    formdef.refresh_from_storage()
    assert formdef.fields[0].post_conditions == [
        {'condition': {'type': 'django', 'value': 'foo'}, 'error_message': 'bar'},
        {'condition': {'type': 'django', 'value': 'foo3'}, 'error_message': 'bar2'},
    ]

    # check error in expression
    resp = resp.click(href=r'^%s/$' % formdef.fields[0].id)
    resp.form['post_conditions$element1$condition$type'] = 'django'
    resp.form['post_conditions$element1$condition$value_django'] = 'foo3 >'
    resp = resp.form.submit('submit')
    assert 'syntax error: Unexpected end of expression in if tag.' in resp.text


def test_form_edit_comment_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.CommentField(id='1', label='a comment field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert 'a comment field' in resp.text
    assert 'WysiwygTextWidget' in resp.text

    # legacy, double line breaks will be converted to paragraphs
    formdef.fields = [fields.CommentField(id='1', label='a comment field\n\na second line')]
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert 'WysiwygTextWidget' in resp.text
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].label == '<p>a comment field</p>\n<p>a second line</p>'

    # starting with a <
    formdef.fields = [fields.CommentField(id='1', label='<strong>a comment field\n\na second line</strong>')]
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert 'WysiwygTextWidget' in resp.text

    # legacy, ezt syntax in a non-html field will be presented as a textarea
    formdef.fields = [fields.CommentField(id='1', label='[if-any toto]hello world[end]')]
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert 'WysiwygTextWidget' not in resp.text

    # check a new field is created with label as HTML, enclosing label in <p>
    resp = app.get('/backoffice/forms/1/fields/')
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'comment'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    assert FormDef.get(formdef.id).fields[-1].label == '<p>foobar</p>'

    # unless label is already given as HTML
    resp = app.get('/backoffice/forms/1/fields/')
    resp.forms[0]['label'] = '<div>blah</div>'
    resp.forms[0]['type'] = 'comment'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/1/fields/'
    assert FormDef.get(formdef.id).fields[-1].label == '<div>blah</div>'


def test_form_edit_time_range_field(pub):
    create_superuser(pub)
    create_role(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'time-range'
    resp = resp.forms[0].submit().follow()

    resp = resp.click('foobar')

    # prefill is not supported
    assert 'prefill$type' not in resp.form.fields

    # no agendas available, and no cards or json url data source
    assert resp.form['data_source$type'].options == [('None', True, 'None')]

    # free range agenda data source
    data_source = NamedDataSource(name='agenda')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.slug = 'chrono_ds_agenda_meetings_test'
    data_source.external = 'agenda'
    data_source.external_type = 'free_range'
    data_source.store()

    # meeting agenda data source, should not appear
    data_source = NamedDataSource(name='normal agenda')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.slug = 'chrono_ds_agenda_meetings_test_no_free_range'
    data_source.external = 'agenda'
    data_source.store()

    resp = app.get(resp.request.url)
    assert resp.form['data_source$type'].options == [
        ('None', True, 'None'),
        ('chrono_ds_agenda_meetings_test', False, 'agenda'),
    ]

    resp.form['data_source$type'] = 'chrono_ds_agenda_meetings_test'
    resp = resp.form.submit('submit').follow()

    resp = resp.click('foobar')
    assert resp.form['data_source$type'].value == 'chrono_ds_agenda_meetings_test'


def test_form_comment_field_textwidget_validation(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    # legacy, ezt syntax in a non-html field will be presented as a textarea
    formdef.fields = [fields.CommentField(id='1', label='[if-any toto]hello world[end]')]
    formdef.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')

    # bad {% %} Django template syntax
    assert 'WysiwygTextWidget' not in resp.text
    resp.form.fields['label'][0].value = '{% if cond %}no endif provided'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template: Unclosed tag on line 1' in resp.text

    # bad {{ }} Django template syntax
    assert 'WysiwygTextWidget' not in resp.text
    resp.form.fields['label'][0].value = '{{0+0}}'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template: Could not parse' in resp.text

    # bad EZT syntax
    assert 'WysiwygTextWidget' not in resp.text
    resp.form.fields['label'][0].value = '[end]'
    resp = resp.form.submit('submit')
    assert 'syntax error in ezt template: unmatched [end]' in resp.text

    # good syntax
    assert 'WysiwygTextWidget' not in resp.text
    resp.form.fields['label'][0].value = '{{variable}}'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].label == '{{variable}}'


def test_form_comment_field_wysiwygtextwidget_validation(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.CommentField(id='1', label='a comment field')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')
    assert 'a comment field' in resp.text

    # bad {% %} Django template syntax
    assert 'WysiwygTextWidget' in resp.text
    resp.form.fields['label'][0].value = '{% if cond %}no endif provided'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template: Unclosed tag on line 1' in resp.text

    # bad {{ }} Django template syntax
    assert 'WysiwygTextWidget' in resp.text
    resp.form.fields['label'][0].value = '{{0+0}}'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template: Could not parse' in resp.text

    # bad EZT syntax
    assert 'WysiwygTextWidget' in resp.text
    resp.form.fields['label'][0].value = '[end]'
    resp = resp.form.submit('submit')
    assert 'syntax error in ezt template: unmatched [end]' in resp.text

    # good syntax
    assert 'WysiwygTextWidget' in resp.text
    resp.form.fields['label'][0].value = '{{variable}}'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].label == '{{variable}}'


def test_form_edit_map_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.MapField(id='1', label='a field')]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/fields/1/')
    resp = resp.form.submit('submit')
    assert resp.location

    # min
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['min_zoom'] = 'Wide area'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].min_zoom == '9'

    # max
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['max_zoom'] = 'Small road'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].max_zoom == '16'

    # both
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['min_zoom'] = 'Wide area'
    resp.form['max_zoom'] = 'Small road'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].min_zoom == '9'
    assert FormDef.get(formdef.id).fields[0].max_zoom == '16'

    # inverted
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['min_zoom'] = 'Small road'
    resp.form['max_zoom'] = 'Wide area'
    resp = resp.form.submit('submit')
    assert 'widget-with-error' in resp.text

    # initial out of range
    formdef.store()
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['initial_zoom'] = 'Whole world'
    resp.form['min_zoom'] = 'Wide area'
    resp.form['max_zoom'] = 'Small road'
    resp = resp.form.submit('submit')
    assert 'widget-with-error' in resp.text

    # prefill fields
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['prefill$type'].value = 'Geolocation'
    resp.form['prefill$value_geolocation'].value = 'Device geolocation'
    resp = resp.form.submit('submit')
    assert FormDef.get(formdef.id).fields[0].prefill == {
        'type': 'geolocation',
        'value': 'position',
        'locked': False,
        'locked-unless-empty': False,
    }


def test_form_edit_field_warnings(pub):
    create_superuser(pub)
    create_role(pub)

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'ignore-hard-limits', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='%d' % i, label='field %d' % i) for i in range(1, 10)]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'more than 200 fields' not in resp.text
    assert 'first field should be of type "page"' not in resp.text

    formdef.fields.append(fields.PageField(id='1000', label='page'))
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'more than 200 fields' not in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert resp.pyquery('#new-field')

    formdef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(10, 210)])
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'more than 200 fields' in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert '>Duplicate<' in resp.text

    formdef.fields.extend([fields.StringField(id='%d' % i, label='field %d' % i) for i in range(210, 410)])
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'This form contains 410 fields.' in resp.text
    assert 'no new fields can be added.' in resp.text
    assert 'first field should be of type "page"' in resp.text
    assert not resp.pyquery('#new-field')
    assert '>Duplicate<' not in resp.text
    assert resp.pyquery('aside .errornotice')
    assert not resp.pyquery('aside form[action=new]')

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'ignore-hard-limits', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert 'no new fields should be added.' in resp.text
    assert resp.pyquery('#new-field')
    assert '>Duplicate<' in resp.text
    assert not resp.pyquery('aside .errornotice')
    assert resp.pyquery('aside form[action=new]')

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
        fields.CommentField(id='345', label='comment'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='Test'),
        fields.BlockField(id='2', label='Block field', block_slug='foobar'),
    ]
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert not resp.pyquery('.warningnotice')
    formdef.fields[1].default_items_count = '1100'
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert (
        resp.pyquery('.warningnotice')
        .text()
        .startswith('There are at least 2201 data fields, including fields in blocks.')
    )

    # no crash if default_items_count is none
    formdef.fields[1].default_items_count = None
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert not resp.pyquery('.warningnotice')

    # and no crash if default_items_count is a template
    formdef.fields[1].default_items_count = '{{ form_var_blah }}'
    formdef.store()
    resp = app.get('/backoffice/forms/%s/fields/' % formdef.id)
    assert not resp.pyquery('.warningnotice')

    FormDef.wipe()


def test_form_limit_display_to_page(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foobar'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2', varname='baz'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3', varname='baz2'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/')
    assert '{{ form_<wbr/>var_<wbr/>foobar }}' in resp.text
    assert '2nd page' in resp.text
    resp = resp.click('Limit display to this page', index=0)
    hidden_fields = ''.join(re.findall('display: none.*', resp.text))
    assert 'All pages' in resp.text
    assert '1st page' not in hidden_fields
    assert '2nd page' in hidden_fields
    assert '{{ form_<wbr/>var_<wbr/>foobar }}' not in hidden_fields
    assert '{{ form_<wbr/>var_<wbr/>baz }}' in hidden_fields

    assert resp.pyquery('.form-pages-navigation a:first-child').hasClass('disabled')
    assert not resp.pyquery('.form-pages-navigation a:last-child').hasClass('disabled')
    resp = resp.click('Next page')
    assert not resp.pyquery('.form-pages-navigation a:first-child').hasClass('disabled')
    assert not resp.pyquery('.form-pages-navigation a:last-child').hasClass('disabled')
    resp = resp.click('Next page')
    assert not resp.pyquery('.form-pages-navigation a:first-child').hasClass('disabled')
    assert resp.pyquery('.form-pages-navigation a:last-child').hasClass('disabled')

    # remove field on current page
    resp = resp.click(href='5/delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/%s/fields/pages/4/#fieldId_4' % formdef.id
    resp = resp.follow()
    # remove current page itself
    resp = resp.click(href='4/delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/forms/%s/fields/#fieldId_3' % formdef.id

    # visit a page that doesn't exist
    app.get('/backoffice/forms/1/fields/pages/123/', status=404)


def test_form_page_field_condition_types(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/0/')
    assert resp.pyquery('[name="condition$type"]').val() == 'django'
    assert resp.pyquery('[name="condition$type"]').attr.type == 'hidden'


def test_form_fields_reorder(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    app = login(get_app(pub))

    # missing element in params: do nothing
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=0;3;1;2;' % formdef.id)
    assert resp.json == {'success': 'ko'}
    # missing order in params: do nothing
    resp = app.get('/backoffice/forms/%s/fields/update_order?element=0' % formdef.id)
    assert resp.json == {'success': 'ko'}

    resp = app.get('/backoffice/forms/%s/fields/update_order?order=0;3;1;2;&element=3' % formdef.id)
    assert resp.json == {'success': 'ok'}
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['0', '3', '1', '2']

    # unknown id: ignored
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=0;1;2;3;4;&element=3' % formdef.id)
    assert resp.json == {'success': 'ok'}
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['0', '1', '2', '3']
    # missing id: do nothing
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=0;3;1;&element=3' % formdef.id)
    assert resp.json == {'success': 'ko'}
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['0', '1', '2', '3']

    # move a page
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=2;0;1;3;&element=2' % formdef.id)
    assert resp.json == {
        'success': 'ok',
        'additional-action': {
            'message': 'Also move the fields of the page',
            'url': 'move_page_fields?fields=3&page=2',
        },
    }
    # reset
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=0;1;2;3;&element=2' % formdef.id)
    assert resp.json == {'success': 'ok'}
    # move the first page
    resp = app.get('/backoffice/forms/%s/fields/update_order?order=1;2;3;0;&element=0' % formdef.id)
    assert resp.json == {
        'success': 'ok',
        'additional-action': {
            'message': 'Also move the fields of the page',
            'url': 'move_page_fields?fields=1&page=0',
        },
    }


def test_form_move_page_fields(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='2', label='2nd page'),
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    app = login(get_app(pub))
    # missing element in params: do nothing
    app.get('/backoffice/forms/%s/fields/move_page_fields?fields=3' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['2', '0', '1', '3']
    # missing order in params: do nothing
    app.get('/backoffice/forms/%s/fields/move_page_fields?page=2' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['2', '0', '1', '3']

    # unknown id: do nothing
    app.get('/backoffice/forms/%s/fields/move_page_fields?fields=4&page=2' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['2', '0', '1', '3']

    # move the fields of the page
    app.get('/backoffice/forms/%s/fields/move_page_fields?fields=3&page=2' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['2', '3', '0', '1']

    # move the new first page
    app.get('/backoffice/forms/%s/fields/update_order?order=3;0;1;2&element=2' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['3', '0', '1', '2']
    # and the fields
    app.get('/backoffice/forms/%s/fields/move_page_fields?fields=3&page=2' % formdef.id)
    formdef = FormDef.get(formdef.id)
    assert [x.id for x in formdef.fields] == ['0', '1', '2', '3']


def test_form_legacy_int_id(pub):
    create_superuser(pub)
    create_role(pub)

    Category.wipe()
    cat = Category(name='Foo')
    cat.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    # create it with a single status
    workflow.possible_status = [Workflow.get_default_workflow().possible_status[-1]]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []

    role = pub.role_class(name='ZAB')  # Z to get sorted last
    role.store()

    # set attributes using integers
    formdef.category_id = int(cat.id)
    formdef.workflow_id = int(workflow.id)
    formdef.workflow_roles = {'_receiver': int(role.id)}
    formdef.roles = ['logged-users', int(role.id)]

    formdef.store()

    formdef = FormDef.get(formdef.id)  # will run migrate

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')

    resp = resp.click(href='category')
    assert resp.forms[0]['category_id'].value

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='workflow', index=1)
    assert resp.forms[0]['workflow_id'].value

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('User Roles')
    assert resp.forms[0]['roles$element0'].value == 'logged-users'
    assert resp.forms[0]['roles$element1'].value == role.id

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('Recipient')
    assert resp.forms[0]['role_id'].value == role.id


def test_form_public_url(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('Display public URL')
    assert 'http://example.net/form-title/' in resp.text


def test_form_management_view(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert 'backoffice/management/form-title/' in resp


def test_form_overwrite(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form test'
    formdef.table_name = 'xxx'
    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.StringField(id='2', label='2nd field'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo', '2': 'bar'}
    formdata.just_created()
    formdata.store()

    formdef_id = formdef.id
    formdef.fields[0].label = '1st modified field'
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef_id)
    resp = resp.click(href='overwrite')
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()
    assert 'Overwrite - Summary of changes' in resp.text
    resp = resp.forms[0].submit()
    assert FormDef.get(formdef_id).fields[0].label == '1st modified field'
    resp = resp.follow()
    assert 'The form has been successfully overwritten.' in resp.text

    # check with added/removed field
    new_formdef = FormDef()
    new_formdef.name = 'form test overwrite'
    new_formdef.fields = [
        fields.StringField(id='2', label='2nd field'),
        fields.StringField(id='3', label='3rd field'),
    ]
    new_formdef_xml = ET.tostring(new_formdef.export_to_xml(include_id=True))

    # and no data within
    formdef.data_class().wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef_id)
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', new_formdef_xml)
    resp = resp.forms[0].submit()
    assert FormDef.get(formdef_id).fields[0].id == '2'
    assert FormDef.get(formdef_id).fields[0].label == '2nd field'
    assert FormDef.get(formdef_id).fields[1].id == '3'
    assert FormDef.get(formdef_id).fields[1].label == '3rd field'

    # and data within
    formdef.store()
    formdata.data = {'1': 'foo', '2': 'bar'}
    formdata.just_created()
    formdata.store()

    resp = app.get('/backoffice/forms/%s/' % formdef_id)
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', new_formdef_xml)
    resp = resp.forms[0].submit()
    assert 'The form removes or changes fields' in resp.text
    assert resp.forms[0]['force'].checked is False
    resp = resp.forms[0].submit()  # without checkbox (back to same form)
    resp.forms[0]['force'].checked = True
    resp = resp.forms[0].submit()

    assert FormDef.get(formdef_id).fields[0].id == '2'
    assert FormDef.get(formdef_id).fields[0].label == '2nd field'
    assert FormDef.get(formdef_id).fields[1].id == '3'
    assert FormDef.get(formdef_id).fields[1].label == '3rd field'

    # check with a field of different type
    formdef = FormDef.get(formdef_id)
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo', '2': 'bar', '3': 'baz'}
    formdata.just_created()
    formdata.store()

    new_formdef = FormDef()
    new_formdef.name = 'form test overwrite'
    new_formdef.fields = [
        fields.StringField(id='2', label='2nd field'),
        fields.DateField(id='3', label='3rd field, date'),
    ]  # (string -> date)
    new_formdef_xml = ET.tostring(new_formdef.export_to_xml(include_id=True))

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef_id)
    resp = resp.click(href='overwrite', index=0)
    resp.forms[0]['file'] = Upload('formdef.wcs', new_formdef_xml)
    resp = resp.forms[0].submit()
    assert 'The form removes or changes fields' in resp.text
    resp.forms[0]['force'].checked = True
    resp = resp.forms[0].submit()
    assert FormDef.get(formdef_id).fields[1].id == '3'
    assert FormDef.get(formdef_id).fields[1].label == '3rd field, date'
    assert FormDef.get(formdef_id).fields[1].key == 'date'

    # check we kept stable references
    assert FormDef.get(formdef_id).url_name == 'form-test'
    assert FormDef.get(formdef_id).table_name == 'xxx'

    # check existing data
    data = FormDef.get(formdef_id).data_class().get(formdata.id).data
    assert data.get('2') == 'bar'
    # in SQL, check data with different type has been removed
    assert data.get('3') is None

    # check with invalid file
    resp = app.get('/backoffice/forms/%s/overwrite' % formdef_id)
    resp.forms[0]['file'] = Upload('formdef.wcs', b'broken data')
    resp = resp.forms[0].submit()
    assert resp.pyquery('.error').text() == 'Invalid File'


def test_form_export_import_export_overwrite(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.table_name = 'xxx'
    formdef.fields = [  # unordered id
        fields.StringField(id='1', label='field 1'),
        fields.DateField(id='12', label='field 2'),
        fields.ItemField(id='4', label='field 3'),
    ]
    formdef.store()

    # add data
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo'}
    formdata.just_created()
    formdata.store()

    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    assert FormDef.count() == 1
    assert formdef.url_name == 'form-title'

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()
    assert FormDef.count() == 2

    formdef2 = FormDef.get(2)
    assert formdef2.url_name == 'form-title-1'
    # fields are imported with original ids
    for i, field in enumerate(formdef.fields):
        field2 = formdef2.fields[i]
        assert (field.id, field.label, field.key) == (field2.id, field2.label, field2.key)

    # modify imported formdef, then overwrite original formdef with it
    formdef2.fields.insert(2, fields.StringField(id='2', label='field 4'))
    formdef2.fields.insert(3, fields.DateField(id='3', label='field 5'))
    formdef2.fields.append(fields.ItemField(id='5', label='field 6'))
    formdef2.store()
    formdef2_xml = ET.tostring(formdef2.export_to_xml(include_id=True))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef2_xml)
    resp = resp.forms[0].submit()
    assert 'Overwrite - Summary of changes' in resp.text
    resp = resp.forms[0].submit()
    formdef_overwrited = FormDef.get(formdef.id)
    for i, field in enumerate(formdef2.fields):
        field_ow = formdef_overwrited.fields[i]
        assert (field.id, field.label, field.key) == (field_ow.id, field_ow.label, field_ow.key)


def test_form_overwrite_from_url(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form test'
    formdef.table_name = 'xxx'
    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.StringField(id='2', label='2nd field'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo', '2': 'bar'}
    formdata.just_created()
    formdata.store()

    formdef_id = formdef.id
    formdef.fields[0].label = '1st modified field'
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef_id)
    resp = resp.click(href='overwrite')
    resp.forms[0]['url'] = 'http://example.net/formdef.wcs'
    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/formdef.wcs', body=ConnectionError('...'))
        resp = resp.forms[0].submit()
        assert 'Error loading form' in resp.text
        rsps.get('http://example.net/formdef.wcs', body=formdef_xml.decode())
        resp = resp.forms[0].submit()
    assert 'Overwrite - Summary of changes' in resp.text
    resp = resp.forms[0].submit()
    assert FormDef.get(formdef_id).fields[0].label == '1st modified field'
    resp = resp.follow()
    assert 'The form has been successfully overwritten.' in resp.text


def test_form_with_custom_views_import_export_overwrite(pub):
    user = create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.table_name = 'xxx'
    formdef.fields = [  # unordered id
        fields.StringField(id='1', label='field 1'),
        fields.DateField(id='12', label='field 2'),
        fields.ItemField(id='4', label='field 3'),
    ]
    formdef.store()

    # add data
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo'}
    formdata.just_created()
    formdata.store()

    # add custom view
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    # add private custom view
    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'private custom test view'
    custom_view2.formdef = formdef
    custom_view2.visibility = 'owner'
    custom_view2.user_id = str(user.id)
    custom_view2.columns = {'list': [{'id': 'id'}]}
    custom_view2.filters = {}
    custom_view2.store()

    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))

    # alter initial custom view
    custom_view.title = 'modified custom test view'
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click(href='overwrite')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()  # -> confirmation
    resp = resp.forms[0].submit()  # -> overwrite
    assert {x.title for x in pub.custom_view_class.select()} == {
        'custom test view',
        'private custom test view',
    }


def test_form_comment_with_error_in_wscall(http_requests, pub):
    create_superuser(pub)
    NamedWsCall.wipe()

    wscall = NamedWsCall(name='xxx')
    wscall.description = 'description'
    wscall.request = {
        'url': 'http://remote.example.net/404',
        'request_signature_key': 'xxx',
        'qs_data': {'a': 'b'},
        'method': 'POST',
        'post_data': {'c': 'd'},
    }
    wscall.record_on_errors = True
    wscall.notify_on_errors = True
    wscall.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.CommentField(id='1', label='x [webservice.xxx.foobar] x')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert 'x [webservice.xxx.foobar] x' in resp.text
    assert LoggedError.count() == 0  # error not recorded


def test_form_new_computed_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'computed'
    resp = resp.forms[0].submit().follow()

    assert len(FormDef.get(1).fields) == 1
    field = FormDef.get(1).fields[0]
    assert field.key == 'computed'
    assert field.label == 'foobar'
    assert field.varname == 'foobar'

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.digest_templates = {'default': 'plop'}
    carddef.store()
    resp = app.get('/backoffice/forms/%s/fields/%s/' % (formdef.id, field.id))
    # only cards
    assert resp.form['data_source$type'].options == [('None', True, 'None'), ('carddef:baz', False, 'Baz')]


def test_form_edit_computed_field(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click(href='fields/')

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'computed'
    resp = resp.forms[0].submit().follow()
    field = FormDef.get(1).fields[0]

    resp = app.get('/backoffice/forms/%s/fields/%s/' % (formdef.id, field.id))
    assert resp.pyquery('input#form_value_template').attr.size == '150'
    resp.form['value_template'] = '{% with %}'  # invalid syntax
    resp = resp.form.submit('submit')
    assert 'syntax error' in resp.pyquery('#form_error_value_template').text()

    resp.form['value_template'] = '{{ "%s" }}' % ('xxxx ' * 200)  # long string
    resp = resp.form.submit('submit')

    # check a long template value is displayed in a textarea
    resp = app.get('/backoffice/forms/%s/fields/%s/' % (formdef.id, field.id))
    assert resp.pyquery('textarea#form_value_template').attr.rows == '7'

    # check template validation is still effective
    resp.form['value_template'] = '{% with %}'  # invalid syntax
    resp = resp.form.submit('submit')
    assert 'syntax error' in resp.pyquery('#form_error_value_template').text()


def test_form_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/forms/', status=403)

    Category.wipe()
    cat = Category(name='Foo')
    cat.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.category_id = cat.id
    formdef.fields = []
    formdef.store()

    cat = Category(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/forms/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'form title' not in resp.text  # formdef in that category
    assert 'Bar' not in resp.text  # not yet any form in this category

    app.get('/backoffice/forms/%s/' % formdef.id, status=403)

    resp = resp.click('New Form')
    resp.forms[0]['name'] = 'form in category'
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user
    resp = resp.forms[0].submit().follow()
    new_formdef = FormDef.get_by_urlname('form-in-category')

    # check category select only let choose one
    resp = resp.click(href='/category')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user

    resp = app.get('/backoffice/forms/')
    assert 'Bar' in resp.text  # now there's a form in this category
    assert 'form in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    assert 'href="data-sources/"' not in resp.text
    assert 'href="blocks/"' not in resp.text
    app.get('/backoffice/forms/categories/', status=403)
    app.get('/backoffice/forms/data-sources/', status=403)
    app.get('/backoffice/forms/blocks/', status=403)

    # no import into other category
    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('formdef.wcs', formdef_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    # check access to inspect page
    formdef.workflow_roles = {'_receiver': int(backoffice_role.id)}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = app.get(formdata.get_backoffice_url())
    assert 'inspect' not in resp.text
    resp = app.get(formdata.get_backoffice_url() + 'inspect', status=403)

    new_formdef.workflow_roles = {'_receiver': int(backoffice_role.id)}
    new_formdef.store()

    formdata = new_formdef.data_class()()
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_backoffice_url())
    assert 'inspect' in resp.text
    resp = app.get(formdata.get_backoffice_url() + 'inspect')


def test_form_restricted_access_import_error(pub, backoffice_user, backoffice_role):
    Category.wipe()
    cat = Category(name='Foo')
    cat.management_roles = [backoffice_role]
    cat.store()
    FormDef.wipe()

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/forms/import')
    resp.forms[0]['file'] = Upload('formdef.wcs', b'broken content')
    resp = resp.forms[0].submit()
    assert 'Invalid File' in resp.text
    assert FormDef.count() == 0


def test_form_preview_edit_page_fields(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='first page'),
        fields.StringField(id='2', label='a field'),
        fields.StringField(id='3', label='another field'),
        fields.PageField(id='4', label='second page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert resp.pyquery('fieldset.formpage a')
    assert [x.attrib['href'] for x in resp.pyquery('fieldset.formpage a')] == [
        'fields/pages/1/',
        'fields/pages/4/',
    ]
    resp = resp.click('edit page fields', index=0)
    assert '<h2>form title - page 1 - first page</h2>' in resp.text
    resp = resp.click(href=r'^1/$')
    assert '/backoffice/forms/1/fields/pages/1/#fieldId_1' in resp
    assert '/backoffice/forms/1/fields/pages/1/1/' in resp  # without anchor


def test_field_display_locations_statistics_choice(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='0', label='String field', varname='var_1'),
        fields.ItemField(id='1', label='Item field'),
        fields.ItemsField(id='2', label='Items field'),
        fields.BoolField(id='3', label='Bool field'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/fields/0/' % formdef.id)
    assert 'Statistics' not in resp.text

    for i in range(1, 4):
        resp = app.get('/backoffice/forms/%s/fields/%s/' % (formdef.id, i))
        assert 'Statistics' in resp.text

        resp.form['display_locations$element3'] = True
        resp = resp.form.submit('submit')
        assert 'Field must have a varname in order to be displayed in statistics.' in resp.text
        assert 'statistics' not in FormDef.get(formdef.id).fields[i].display_locations

        resp.form['varname'] = 'var_%s' % i
        resp = resp.form.submit('submit')
        assert 'statistics' in FormDef.get(formdef.id).fields[i].display_locations


def test_admin_form_inspect(pub):
    user = create_superuser(pub)
    create_role(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='Foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/404'}
    data_source.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.digest_templates = {'default': 'plop'}
    carddef.store()
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.store()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.TitleField(id='0', label='option title'),
        fields.StringField(id='1', varname='test', label='Test'),
    ]
    workflow.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'false'}, 'error_message': 'You shall not pass.'}
            ],
        ),
        fields.StringField(
            id='1', label='String field', varname='var_1', condition={'type': 'django', 'value': 'true'}
        ),
        fields.ItemField(
            id='4',
            label='Date field',
        ),
        fields.ItemField(id='5', label='Item field', items=['One', 'Two', 'Three']),
        fields.ItemField(id='6', label='Item field named data source', data_source={'type': 'foobar'}),
        fields.ItemField(id='7', label='Item field carddef data source', data_source={'type': 'carddef:baz'}),
        fields.ItemField(
            id='70',
            label='Item field carddef data source',
            data_source={'type': 'carddef:baz:custom'},
        ),
        fields.ItemField(
            id='8',
            label='Item field json data source',
            data_source={'type': 'json', 'value': 'http://test'},
        ),
        fields.ItemsField(id='9', label='Items field', items=['One', 'Two', 'Three']),
        fields.StringField(id='10', label='prefill', prefill={'type': 'user', 'value': 'email'}),
        fields.StringField(
            id='11', label='prefill2', prefill={'type': 'string', 'value': '{{plop}}', 'locked': True}
        ),
        fields.FileField(id='12', label='file', display_locations=['validation']),
        fields.BlockField(id='13', label='Block field', block_slug='foobar'),
        fields.ItemField(id='14', label='Item field invalid data source', data_source={'type': 'xxx'}),
        fields.FileField(
            id='15', label='file', automatic_image_resize=False, display_locations=['validation']
        ),
    ]
    formdef.workflow_options = {'test': 'plop'}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)

    assert 'Test → plop' in resp.text  # workflow option
    assert '<strong>option title</strong>' in resp.text  # title field as workflow option
    assert (
        resp.pyquery('[data-field-id="0"] .parameter-post_conditions').text()
        == 'Post Conditions:\nfalse (Django) - You shall not pass.'
    )
    assert (
        resp.pyquery('[data-field-id="1"] .parameter-condition').text() == 'Display Condition: true (Django)'
    )
    assert resp.pyquery('[data-field-id="5"] .parameter-items').text() == 'Choices: One, Two, Three'
    assert resp.pyquery('[data-field-id="6"] .parameter-data_source').text() == 'Data source: Foobar'
    assert resp.pyquery('[data-field-id="6"] .parameter-data_source a').text() == 'Foobar'
    assert (
        resp.pyquery('[data-field-id="6"] .parameter-data_source a').attr['href']
        == 'http://example.net/backoffice/settings/data-sources/1/'
    )
    assert resp.pyquery('[data-field-id="7"] .parameter-data_source').text() == 'Data source: card model: Baz'
    assert resp.pyquery('[data-field-id="7"] .parameter-data_source a').text() == 'card model: Baz'
    assert (
        resp.pyquery('[data-field-id="7"] .parameter-data_source a').attr['href']
        == 'http://example.net/backoffice/cards/1/'
    )
    assert (
        resp.pyquery('[data-field-id="70"] .parameter-data_source').text()
        == 'Data source: card model: Baz, custom view: custom'
    )
    assert (
        PyQuery(resp.pyquery('[data-field-id="70"] .parameter-data_source a')[0]).text() == 'card model: Baz'
    )
    assert (
        PyQuery(resp.pyquery('[data-field-id="70"] .parameter-data_source a')[0]).attr['href']
        == 'http://example.net/backoffice/cards/1/'
    )
    assert (
        PyQuery(resp.pyquery('[data-field-id="70"] .parameter-data_source a')[1]).text()
        == 'custom view: custom'
    )
    assert (
        PyQuery(resp.pyquery('[data-field-id="70"] .parameter-data_source a')[1]).attr['href']
        == 'http://example.net/backoffice/data/baz/custom'
    )
    assert (
        resp.pyquery('[data-field-id="8"] .parameter-data_source').text()
        == 'Data source: JSON URL - http://test'
    )
    assert (
        resp.pyquery('[data-field-id="10"] .parameter-prefill').text()
        == 'Prefill:\nType: User Field\nValue: Email (builtin)'
    )
    assert (
        resp.pyquery('[data-field-id="11"] .parameter-prefill').text()
        == 'Prefill:\nType: String / Template\nValue: {{plop}}\nLocked'
    )
    assert not resp.pyquery('[data-field-id="12"] .parameter-automatic_image_resize')
    assert (
        resp.pyquery('[data-field-id="12"] .parameter-display_locations').text()
        == 'Display Locations: Validation Page'
    )
    assert resp.pyquery('[data-field-id="13"] h4 .inspect-field-type a').attr.href.endswith(
        block.get_admin_url() + 'inspect'
    )
    assert resp.pyquery('[data-field-id="14"] .parameter-data_source a').attr['href'] == '#invalid-xxx'

    assert (
        resp.pyquery('[data-field-id="15"] .parameter-automatic_image_resize').text()
        == 'Automatically resize uploaded images: No'
    )

    assert '>Custom views</button>' not in resp

    # check all field links
    for href in [x.attrib['href'] for x in resp.pyquery('.inspect-field h4 a')]:
        app.get(href)

    # check field links targets per-page URL
    assert '/pages/' in resp.pyquery('.inspect-field h4 a')[0].attrib['href']

    custom_view_owner = pub.custom_view_class()
    custom_view_owner.title = 'card view owner'
    custom_view_owner.formdef = formdef
    custom_view_owner.visibility = 'owner'
    custom_view_owner.store()
    custom_view_role = pub.custom_view_class()
    custom_view_role.title = 'card view role'
    custom_view_role.formdef = formdef
    custom_view_role.visibility = 'role'
    custom_view_role.store()
    custom_view_any = pub.custom_view_class()
    custom_view_any.title = 'card view any'
    custom_view_any.formdef = formdef
    custom_view_any.visibility = 'any'
    custom_view_any.store()
    custom_view_datasource = pub.custom_view_class()
    custom_view_datasource.title = 'card view datasource'
    custom_view_datasource.formdef = formdef
    custom_view_datasource.visibility = 'datasource'
    custom_view_datasource.author = user
    custom_view_datasource.store()

    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)
    assert '>Custom views</button>' in resp
    assert not resp.pyquery(f'#inspect-customviews--{custom_view_owner.id}').text()
    assert resp.pyquery(f'#inspect-customviews--{custom_view_role.id}').text() == 'card view role'
    assert resp.pyquery(f'#inspect-customviews--{custom_view_any.id}').text() == 'card view any'
    assert not resp.pyquery(f'#inspect-customviews--{custom_view_any.id} + ul li.parameter--author').text()
    assert resp.pyquery(f'#inspect-customviews--{custom_view_datasource.id}').text() == 'card view datasource'
    assert (
        resp.pyquery(f'#inspect-customviews--{custom_view_datasource.id} + ul li.parameter--author').text()
        == 'Author: admin'
    )

    # check with a form without pages
    formdef.fields = [
        fields.StringField(
            id='1', label='String field', varname='var_1', condition={'type': 'django', 'value': 'true'}
        ),
    ]
    formdef.store()
    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)

    # check all field links
    for href in [x.attrib['href'] for x in resp.pyquery('.inspect-field h4 a')]:
        app.get(href)

    # check field links targets per-page URL
    assert '/pages/' not in resp.pyquery('.inspect-field h4 a')[0].attrib['href']

    # check drafts lifespan value
    assert [
        PyQuery(x).parent().text()
        for x in resp.pyquery('.parameter')
        if x.text == 'Lifespan of drafts (in days):'
    ] == ['Lifespan of drafts (in days): 100']
    formdef.drafts_lifespan = '40'
    formdef.store()
    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)
    assert [
        PyQuery(x).parent().text()
        for x in resp.pyquery('.parameter')
        if x.text == 'Lifespan of drafts (in days):'
    ] == ['Lifespan of drafts (in days): 40']


def test_admin_form_inspect_validation(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='String field digits', validation={'type': 'digits'}),
        fields.StringField(id='2', label='String field regex', validation={'type': 'regex', 'value': r'\d+'}),
        fields.StringField(
            id='3', label='String field django', validation={'type': 'django', 'value': 'value == "plop"'}
        ),
        fields.StringField(id='4', label='String field missing django', validation={'type': 'django'}),
    ]
    formdef.workflow_options = {'test': 'plop'}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)

    assert resp.pyquery('[data-field-id="1"] .parameter-validation').text() == 'Validation: Digits'
    assert (
        resp.pyquery('[data-field-id="2"] .parameter-validation').text()
        == 'Validation: Regular Expression - \\d+'
    )
    assert (
        resp.pyquery('[data-field-id="3"] .parameter-validation').text()
        == 'Validation: Django Condition - value == "plop"'
    )
    assert not resp.pyquery('[data-field-id="4"] .parameter-validation').length


def test_admin_form_inspect_drafts(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)
    assert resp.pyquery('#inspect-drafts p').text() == 'There are currently no drafts for this form.'

    data_class = formdef.data_class()
    for page_id in ('0', '2', '4', '_confirmation_page', 'xxxx'):
        formdata = data_class()
        formdata.status = 'draft'
        formdata.page_id = page_id
        formdata.receipt_time = localtime()
        formdata.store()

    # create a non-draft
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    # create a non-draft but before draft duration
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = localtime() - datetime.timedelta(days=200)
    formdata.store()

    resp = app.get('/backoffice/forms/%s/inspect' % formdef.id)
    assert resp.pyquery('#inspect-drafts h2').text() == 'Key indicators on existing drafts'
    assert resp.pyquery('#inspect-drafts .infonotice').text() == 'Covered period: last 100 days.'

    assert resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="0"]').length == 1
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="0"] td.label').text()
        == '1st page'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="0"] td.percent').text()
        == '20%'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="0"] td.total').text()
        == '(1/5)'
    )

    assert resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="2"]').length == 1
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="2"] td.label').text()
        == '2nd page'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="2"] td.percent').text()
        == '20%'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="2"] td.total').text()
        == '(1/5)'
    )

    assert resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="4"]').length == 1
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="4"] td.label').text()
        == '3rd page'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="4"] td.percent').text()
        == '20%'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="4"] td.total').text()
        == '(1/5)'
    )

    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="_confirmation_page"]').length
        == 1
    )
    assert (
        resp.pyquery(
            'table[data-table-id="rate-among-drafts"] tr[data-page-id="_confirmation_page"] td.label'
        ).text()
        == 'Confirmation page'
    )
    assert (
        resp.pyquery(
            'table[data-table-id="rate-among-drafts"] tr[data-page-id="_confirmation_page"] td.percent'
        ).text()
        == '20%'
    )
    assert (
        resp.pyquery(
            'table[data-table-id="rate-among-drafts"] tr[data-page-id="_confirmation_page"] td.total'
        ).text()
        == '(1/5)'
    )

    assert resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="_unknown"]').length == 1
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="_unknown"] td.label').text()
        == 'Unknown'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="_unknown"] td.percent').text()
        == '20%'
    )
    assert (
        resp.pyquery('table[data-table-id="rate-among-drafts"] tr[data-page-id="_unknown"] td.total').text()
        == '(1/5)'
    )

    # check completion rate
    assert resp.pyquery('.completion-rate .percent').text() == '16.7%'
    assert resp.pyquery('.completion-rate .total').text() == '(1/6)'
    assert 'width: 16.6' in resp.pyquery('.completion-rate .bar span').attr.style


def test_form_import_fields(pub):
    create_superuser(pub)
    create_role(pub)

    CardDef.wipe()
    FormDef.wipe()

    formdef1 = FormDef()
    formdef1.name = 'form title'
    formdef1.fields = [
        fields.StringField(id='1', label='field 1'),
        fields.StringField(id='2', label='field 2'),
    ]
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'form2 title'
    formdef2.fields = [
        fields.StringField(id='1', label='field A'),
    ]
    formdef2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/fields/' % formdef2.id)

    resp = app.get('/backoffice/forms/%s/fields/new' % formdef2.id, status=302).follow()
    assert 'Submitted form was not filled properly.' in resp.text

    resp = app.get('/backoffice/forms/%s/fields/' % formdef2.id)
    resp = resp.forms['import-fields'].submit().follow()
    assert 'Submitted form was not filled properly.' in resp.text

    assert len(resp.forms['import-fields']['form'].options) == 3  # (empty, form1, form2)
    assert resp.pyquery('#import-fields optgroup').length == 0

    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [
        fields.StringField(id=str(uuid.uuid4()), label='field 3'),
        fields.StringField(id='2', label='field 4'),
    ]
    carddef.store()
    cardfield0 = carddef.fields[0]
    cardfield1 = carddef.fields[1]

    resp = app.get('/backoffice/forms/%s/fields/' % formdef2.id)
    assert len(resp.forms['import-fields']['form'].options) == 4  # (empty, form1, form2, card1)
    assert resp.pyquery('#import-fields optgroup').length == 2

    resp.forms['import-fields']['form'] = 'form:%s' % formdef1.id
    resp = resp.forms['import-fields'].submit().follow()
    assert 'Submitted form was not filled properly.' not in resp.text

    formdef2.refresh_from_storage()
    assert [x.label for x in formdef2.fields] == [
        'field A',
        'field 1',
        'field 2',
    ]
    field0, field1, field2 = formdef2.fields

    # import a card
    resp.forms['import-fields']['form'] = 'card:%s' % carddef.id
    resp = resp.forms['import-fields'].submit().follow()
    formdef2.refresh_from_storage()
    assert [x.label for x in formdef2.fields] == [
        'field A',
        'field 1',
        'field 2',
        'field 3',
        'field 4',
    ]
    assert [f.id for f in formdef2.fields[:3]] == [field0.id, field1.id, field2.id]
    assert [f.id for f in formdef2.fields[3:]] != [cardfield0.id, cardfield1.id]

    # import while on a specific page
    formdef3 = FormDef()
    formdef3.name = 'form3 title'
    formdef3.fields = [
        fields.PageField(id='1', label='Page 1'),
        fields.StringField(id='2', label='field 1'),
        fields.PageField(id='3', label='Page 2'),
    ]
    formdef3.store()

    resp = app.get('/backoffice/forms/%s/fields/pages/1/' % formdef3.id)
    resp.forms['import-fields']['form'] = 'card:%s' % carddef.id
    resp = resp.forms['import-fields'].submit().follow()
    formdef3.refresh_from_storage()
    assert [x.label for x in formdef3.fields] == [
        'Page 1',
        'field 1',
        'field 3',
        'field 4',
        'Page 2',
    ]


def test_form_field_statistics_data_update(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BoolField(id='1', label='Bool', varname='bool')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = True
    formdata.store()

    assert 'bool' not in formdata.statistics_data

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/fields/1/')

    resp.form['display_locations$element3'] = True
    resp = resp.form.submit('submit').follow()
    assert 'Statistics data will be collected in the background.' in resp.text

    formdata.refresh_from_storage()
    assert formdata.statistics_data['bool'] == [True]


def test_forms_last_test_results(pub, formdef):
    TestResults.wipe()
    TestDef.wipe()
    create_superuser(pub)
    create_role(pub)

    testdef = TestDef()
    testdef.object_type = formdef.get_table_name()
    testdef.object_id = str(formdef.id)
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    assert 'Last tests run' not in resp.text

    test_results = TestResults()
    test_results.object_type = formdef.get_table_name()
    test_results.object_id = str(formdef.id)
    test_results.timestamp = datetime.datetime(2023, 7, 3, 14, 30)
    test_results.success = True
    test_results.reason = ''
    test_results.results = []
    test_results.store()

    resp = app.get('/backoffice/forms/1/')
    assert 'Last tests run: 2023-07-03 14:30' in resp.text
    assert resp.pyquery('.test-success')
    assert not resp.pyquery('.test-failure')

    resp = resp.click('Last tests run')
    assert 'Result #%s' % test_results.id in resp.text

    test_results.success = False
    test_results.store()

    resp = app.get('/backoffice/forms/1/')
    assert not resp.pyquery('.test-success')
    assert resp.pyquery('.test-failure')

    test_results.success = True
    test_results.id = None
    test_results.store()
    assert TestResults.count() == 2

    resp = app.get('/backoffice/forms/1/')
    assert resp.pyquery('.test-success')
    assert not resp.pyquery('.test-failure')

    TestDef.remove_object(testdef.id)
    resp = app.get('/backoffice/forms/1/')
    assert 'Last tests run' not in resp.text


def test_admin_form_sql_integrity_error(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BoolField(id='1', label='Bool')]
    formdef.store()

    formdef.fields = [fields.StringField(id='1', label='String')]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url())
    assert (
        resp.pyquery('.errornotice summary').text()
        == 'There are integrity errors in the database column types.'
    )
    assert resp.pyquery('.errornotice li').text() == 'String, expected: character varying, got: boolean.'


def test_form_documentation(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BoolField(id='1', label='Bool')]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get(formdef.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(formdef.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    formdef.refresh_from_storage()
    assert formdef.documentation == '<p>doc</p>'
    resp = app.get(formdef.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(formdef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation[hidden]')
    assert resp.pyquery('#sidebar[hidden]')
    resp = app.post_json(formdef.get_admin_url() + 'fields/1/update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    formdef.refresh_from_storage()
    assert formdef.fields[0].documentation == '<p>doc</p>'
    resp = app.get(formdef.get_admin_url() + 'fields/1/')
    assert resp.pyquery('.documentation:not([hidden])')
    assert resp.pyquery('#sidebar:not([hidden])')


def test_forms_quick_search(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))
    create_role(pub)

    formdef = FormDef()
    formdef.name = '1 form title'
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = '2 form with\'quote'
    formdef2.store()

    formdef3 = FormDef()
    formdef3.name = '3 form with’quote'
    formdef3.slug = 'custom-slug'
    formdef3.store()

    resp = app.get('/backoffice/forms/')
    assert [PyQuery(x).attr['data-search-text'] for x in resp.pyquery('[data-search-text]')] == [
        '1-form-title-n1-form-title',
        '2-form-with-quote-n2-form-with-quote',
        '3-form-withquote-custom-slug',
    ]


def test_forms_by_slug(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    assert app.get('/backoffice/forms/by-slug/form-title').location == formdef.get_admin_url()
    assert app.get('/backoffice/forms/by-slug/xxx', status=404)


def test_form_workflow_escape(pub):
    FormDef.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    workflow = Workflow(name='<hello1>')
    workflow.roles = {'_receiver': '<hello2>'}
    workflow.add_status(name='baz')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow = workflow
    formdef.store()

    resp = app.get(formdef.get_admin_url())
    assert '<hello1>' not in resp.text
    assert '<hello2>' not in resp.text
    assert '&lt;hello1&gt;' in resp.text
    assert '&lt;hello2&gt;' in resp.text
