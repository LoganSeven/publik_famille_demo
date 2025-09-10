import datetime
import io
import os
import random
import re
import time
import urllib.parse
import uuid
import zipfile

import pytest
import responses
from django.utils.timezone import make_aware
from webtest import Upload

import wcs.qommon.storage as st
from wcs import fields
from wcs.backoffice.management import MassActionAfterJob
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.forms.actions import GlobalInteractiveMassActionAfterJob
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.upload_storage import PicklableUpload
from wcs.roles import logged_users_role
from wcs.sql import ApiAccess
from wcs.sql_criterias import Contains, Null
from wcs.tracking_code import TrackingCode
from wcs.wf.comment import WorkflowCommentPart
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormEvolutionPart, WorkflowFormFieldsFormDef
from wcs.wf.jump import _apply_timeouts
from wcs.wf.register_comment import JournalEvolutionPart
from wcs.workflows import (
    JumpEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
)
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def create_user(pub, is_admin=False):
    user1 = None
    for user in pub.user_class.select():
        if user.name == 'admin':
            user1 = user
            user1.is_admin = is_admin
            user1.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
            user1.store()
        elif user.email == 'jean.darmette@triffouilis.fr':
            pass  # don't remove user created by local_user fixture
        else:
            user.remove_self()
    if user1:
        return user1
    user1 = pub.user_class(name='admin')
    user1.email = 'admin@localhost'
    user1.is_admin = is_admin
    user1.store()

    account1 = PasswordAccount(id='admin')
    account1.set_password('admin')
    account1.user_id = user1.id
    account1.store()

    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()

    user1.roles = [role.id]
    user1.store()

    return user1


def create_superuser(pub):
    return create_user(pub, is_admin=True)


def create_environment(pub, set_receiver=True):
    pub.session_manager.session_class.wipe()
    Workflow.wipe()
    Category.wipe()
    FormDef.wipe()
    BlockDef.wipe()
    CardDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.enable_tracking_codes = True
    if set_receiver:
        formdef.workflow_roles = {'_receiver': 1}

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }

    formdef.fields = []
    formdef.store()  # make sure sql columns are removed

    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.ItemField(
            id=str(uuid.uuid4()),
            label='2nd field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.ItemField(id='3', label='3rd field', data_source=datasource, varname='foo'),
    ]

    formdef.store()
    formdef.data_class().wipe()
    for i in range(50):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = datetime.datetime(2015, 1, 1, 0, i)
        formdata.data = {'1': 'FOO BAR %d' % i}
        if i % 4 == 0:
            formdata.data[formdef.fields[1].id] = 'foo'
            formdata.data['%s_display' % formdef.fields[1].id] = 'foo'
            formdata.data['3'] = 'A'
            formdata.data['3_display'] = 'aa'
        elif i % 4 == 1:
            formdata.data[formdef.fields[1].id] = 'bar'
            formdata.data['%s_display' % formdef.fields[1].id] = 'bar'
            formdata.data['3'] = 'B'
            formdata.data['3_display'] = 'bb'
        else:
            formdata.data[formdef.fields[1].id] = 'baz'
            formdata.data['%s_display' % formdef.fields[1].id] = 'baz'
            formdata.data['3'] = 'C'
            formdata.data['3_display'] = 'cc'
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()
        code = TrackingCode()
        code.formdata = formdata

    formdata = formdef.data_class()()
    formdata.data = {'1': 'XXX', '2': 'foo', '2_display': 'foo'}
    formdata.status = 'draft'
    formdata.store()

    formdef = FormDef()
    if set_receiver:
        formdef.workflow_roles = {'_receiver': 1}
    formdef.name = 'other form'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()
    for i in range(20):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = datetime.datetime(2014, 1, 1)
        formdata.jump_status('new')
        formdata.store()


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_unlogged(pub):
    create_superuser(pub)
    resp = get_app(pub).get('/backoffice/', status=302)
    assert resp.location == 'http://example.net/login/?next=http%3A%2F%2Fexample.net%2Fbackoffice%2F'


def test_backoffice_home(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/')
    assert resp.location.endswith('/studio/')
    resp.follow()


def test_backoffice_manage_redirect(pub):
    app = get_app(pub)
    assert app.get('/manage', status=302).location == 'http://example.net/backoffice'
    assert app.get('/manage/studio/', status=302).location == 'http://example.net/backoffice/studio/'
    assert (
        app.get('/manage/studio/?param=1', status=302).location
        == 'http://example.net/backoffice/studio/?param=1'
    )


def test_backoffice_role_user(pub):
    create_user(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/')
    assert resp.location.endswith('/management/')

    pub.cfg['admin-permissions'] = {'forms': [x.id for x in pub.role_class.select()]}
    pub.write_cfg()
    resp = app.get('/backoffice/')
    assert resp.location.endswith('/studio/')
    resp = resp.follow()
    assert 'Forms' in resp.text
    assert 'Workflows' not in resp.text

    pub.cfg['admin-permissions'] = {'workflows': [x.id for x in pub.role_class.select()]}
    pub.write_cfg()
    resp = app.get('/backoffice/')
    assert resp.location.endswith('/studio/')
    resp = resp.follow()
    assert 'Forms' not in resp.text
    assert 'Workflows' in resp.text

    # check role id int->str migration
    pub.cfg['admin-permissions'] = {'workflows': [int(x.id) for x in pub.role_class.select()]}
    pub.write_cfg()
    resp = app.get('/backoffice/')
    assert resp.location.endswith('/studio/')
    resp = resp.follow()
    assert 'Forms' not in resp.text
    assert 'Workflows' in resp.text


def test_backoffice_forms(pub):
    create_superuser(pub)
    create_environment(pub, set_receiver=False)

    # 1st time with user not handling those forms
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert 'Forms in your care' not in resp.text
    assert re.findall('Other Forms.*form-title', resp.text)

    # 2nd time with user set as receiver of the forms
    create_environment(pub, set_receiver=True)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert 'Forms in your care' in resp.text
    assert '17 open on 50' in resp.text

    # disable form, make sure it's still displayed
    formdef = FormDef.get_by_urlname('form-title')
    formdef.disabled = True
    formdef.store()
    resp = app.get('/backoffice/management/forms')
    assert 'form-title' in resp.text
    assert '17 open on 50' in resp.text

    formdef.disabled = False
    formdef.store()

    # add an extra status to workflow and move a few formdatas to it, they
    # should then be marked as open but not waiting for actions.
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    st1 = workflow.add_status('Status1')
    jump = st1.add_action('jump', id='_jump')
    jump.timeout = 86400
    jump.mode = 'timeout'
    jump.status = 'finished'
    workflow.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow = workflow
    formdef.store()

    for i, formdata in enumerate(formdef.data_class().select(order_by='id')):
        if formdata.status == 'wf-new' and i % 2:
            formdata.status = 'wf-%s' % st1.id
            formdata.store()

    resp = app.get('/backoffice/management/forms')
    assert 'Forms in your care' in resp.text
    assert '9 open on 50' in resp.text

    # anonymise some formdata, they should no longer be included
    formdef = FormDef.get_by_urlname('form-title')
    for i, formdata in enumerate(
        formdef.data_class().select([st.Equal('status', 'wf-finished')], order_by='id')
    ):
        if i >= 20:
            break
        formdata.anonymise()

    for i, formdata in enumerate(formdef.data_class().select([st.Equal('status', 'wf-new')], order_by='id')):
        if i >= 5:
            break
        formdata.anonymise()

    resp = app.get('/backoffice/management/forms')
    assert 'Forms in your care' in resp.text
    assert '4 open on 25' in resp.text


def test_backoffice_management_css_class(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery.find('body.section-management')


def test_backoffice_form_access_forbidden(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    user = create_user(pub)
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    resp = login(get_app(pub)).get(formdata.get_backoffice_url(), status=403)
    assert 'Access Forbidden' in resp.text
    assert 'data-gadjo="true"' in resp.text  # backoffice style

    role = pub.role_class.get(user.roles[0])
    role.allows_backoffice_access = False
    role.store()
    resp = login(get_app(pub)).get(formdata.get_backoffice_url(), status=403)
    assert 'data-gadjo="true"' not in resp.text  # no style
    role.allows_backoffice_access = True
    role.store()


def test_admin_form_page(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'backoffice/forms/1/' in resp
    assert 'backoffice/workflows/_default/' in resp


def test_backoffice_listing(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 17

    # check status filter <select>
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'all'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('data-link') == 20

    # check status filter <select>
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'done'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('data-link') == 20
    resp = resp.click('Next Page')
    assert resp.text.count('data-link') == 13

    # add an extra status to workflow and move a few formdatas to it, they
    # should then be marked as open but not waiting for actions.
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    st1 = workflow.add_status('Status1')
    st1.id = 'plop'
    jump = st1.add_action('jump', id='_jump')
    jump.timeout = 86400
    jump.mode = 'timeout'
    jump.status = 'finished'
    workflow.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow = workflow
    formdef.store()

    for i, formdata in enumerate(formdef.data_class().select(order_by='id')):
        if formdata.status == 'wf-new' and i % 2:
            formdata.status = 'wf-%s' % st1.id
            formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 9
    resp.forms['listing-settings']['filter'] = 'pending'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('data-link') == 17

    # check status forced as endpoints are not part of the "actionable" list.
    workflow = Workflow.get_default_workflow()
    workflow.id = '3'
    st1 = workflow.add_status('Status1')
    st1.id = 'plop'
    st1.forced_endpoint = False
    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_receiver']
    again.status = st1.id
    workflow.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().rebuild_security()

    for i, formdata in enumerate(formdef.data_class().select(order_by='id')):
        if formdata.status == 'wf-new' and i % 2:
            formdata.status = 'wf-%s' % st1.id
            formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 17
    resp.forms['listing-settings']['filter'] = 'pending'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('data-link') == 17

    # mark status as an endpoint
    st1.forced_endpoint = True
    workflow.store()
    formdef.data_class().rebuild_security()

    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 9
    resp.forms['listing-settings']['filter'] = 'pending'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('data-link') == 9


def test_backoffice_listing_pagination(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 17

    resp = app.get('/backoffice/management/form-title/?limit=5')
    assert resp.text.count('data-link') == 5
    assert '<div id="page-links">' in resp.text

    resp = resp.click(re.compile('^2$'))  # second page
    assert resp.text.count('data-link') == 5
    assert resp.forms['listing-settings']['offset'].value == '5'

    resp = resp.click(re.compile('^3$'))  # third page
    assert resp.text.count('data-link') == 5
    assert resp.forms['listing-settings']['offset'].value == '10'

    resp = resp.click(re.compile('^4$'))  # fourth page
    assert resp.text.count('data-link') == 2
    assert resp.forms['listing-settings']['offset'].value == '15'

    with pytest.raises(IndexError):  # no fifth page
        resp = resp.click(re.compile('^5$'))

    resp = resp.click(re.compile('^10$'))  # per page: 10
    assert resp.text.count('data-link') == 10

    resp = resp.click(re.compile('^20$'))  # per page: 20
    assert resp.text.count('data-link') == 17

    # try an overbound offset
    resp = app.get('/backoffice/management/form-title/?limit=5&offset=30')
    resp = resp.follow()
    assert resp.forms['listing-settings']['offset'].value == '0'

    # try invalid values
    resp = app.get('/backoffice/management/form-title/?limit=toto&offset=30', status=400)
    resp = app.get('/backoffice/management/form-title/?limit=5&limit=5&offset=30', status=400)
    resp = app.get('/backoffice/management/form-title/?limit=5&offset=toto', status=400)
    resp = app.get('/backoffice/management/form-title/?limit=5&offset=30&offset=30', status=400)


def test_backoffice_listing_anonymised(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?limit=500')
    assert resp.text.count('data-link') == 17

    formdef = FormDef.get_by_urlname('form-title')
    for i, formdata in enumerate(formdef.data_class().select(order_by='id')):
        if i % 2:
            formdata.anonymise()

    resp = app.get('/backoffice/management/form-title/?limit=500')
    assert resp.text.count('data-link') == 9


def test_backoffice_anonymise_no_actions(pub):
    user = create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test anonymise'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.fields = [
        fields.StringField(id='1', label='1st field', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = 'plop'
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())
    assert 'wf-actions' in resp.forms

    formdata.anonymise()
    resp = app.get(formdata.get_backoffice_url())
    assert 'wf-actions' not in resp.forms


@pytest.mark.parametrize('wcs_fts', [True, False])
def test_backoffice_listing_fts(pub, wcs_fts):
    pub.site_options.set('options', 'enable-new-fts', 'true' if wcs_fts else 'false')

    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 17
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'all'
    resp.forms['listing-settings']['limit'] = '100'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 50
    assert [x.text for x in resp.pyquery('tbody tr .cell-id a')] == [
        '%s-%s' % (formdef.id, i) for i in range(50, 0, -1)
    ]

    # search on text (foo is on all formdata so it gets the same set of results, but ordered differently)
    resp.forms['listing-settings']['q'] = 'foo'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 50
    assert {x.text for x in resp.pyquery('tbody tr .cell-id a')} == {
        '%s-%s' % (formdef.id, i) for i in range(50, 0, -1)
    }  # same set
    assert [x.text for x in resp.pyquery('tbody tr .cell-id a')] != [
        '%s-%s' % (formdef.id, i) for i in range(50, 0, -1)
    ]  # but different order
    # get first row, check it has b'foo' in its item field
    formdata = formdef.data_class().get(resp.pyquery('tbody tr .cell-id a')[0].attrib['href'].strip('/'))
    assert formdata.data[formdef.fields[1].id] == 'foo'

    resp.forms['listing-settings']['q'] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 24
    results = [x.text for x in resp.pyquery('tbody tr .cell-id a')]
    # force order, check it's same set but different order
    resp.forms['listing-settings']['order_by'] = '-receipt_time'
    resp = resp.forms['listing-settings'].submit().follow()
    assert {x.text for x in resp.pyquery('tbody tr .cell-id a')} == set(results)
    assert [x.text for x in resp.pyquery('tbody tr .cell-id a')] != results

    # check search by tracking code is disabled
    tracking_codes = [formdata.tracking_code for formdata in formdef.data_class().select()]
    resp.forms['listing-settings']['q'] = tracking_codes[0]
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 0


def test_backoffice_legacy_urls(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/form-title/')
    assert resp.location == 'http://example.net/backoffice/management/form-title/'
    resp = app.get('/backoffice/form-title/%s/' % formdata.id)
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata.id
    resp = app.get('/backoffice/form-title/listing/?bla')
    assert resp.location == 'http://example.net/backoffice/management/form-title/listing/?bla'
    resp = app.get('/backoffice/form-title/listing/foo?bla')
    assert resp.location == 'http://example.net/backoffice/management/form-title/listing/foo?bla'
    resp = app.get('/backoffice/not-form-title/', status=404)


def test_backoffice_form_category_permissions(pub):
    user = create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' in resp.text

    cat1 = Category(name='cat1')
    cat1.store()
    formdef.category_id = cat1.id
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' in resp.text

    role = pub.role_class(name='limited perms')
    role.store()
    cat1.export_roles = [role]
    cat1.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' not in resp.text

    cat1.statistics_roles = [role]
    cat1.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' not in resp.text
    app.get('/backoffice/management/form-title/stats', status=403)
    app.get('/backoffice/management/form-title/export-spreadsheet', status=403)
    app.get('/backoffice/management/form-title/csv', status=403)
    app.get('/backoffice/management/form-title/ods', status=403)
    # check it's not displayed anymore in global statistics
    resp = app.get('/backoffice/management/statistics')
    assert 'cat1' not in resp.text

    # check it's ok for admins
    user.is_admin = True
    user.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' in resp.text
    app.get('/backoffice/management/form-title/stats', status=200)
    app.get('/backoffice/management/form-title/export-spreadsheet', status=200)
    app.get('/backoffice/management/form-title/csv', status=200)
    app.get('/backoffice/management/form-title/ods', status=200)
    resp = app.get('/backoffice/management/statistics')
    assert 'cat1' in resp.text

    # check it's ok for agents with roles
    user.is_admin = False
    user.roles.append(role.id)
    user.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'Export a Spreadsheet' in resp.text
    app.get('/backoffice/management/form-title/stats', status=200)
    app.get('/backoffice/management/form-title/export-spreadsheet', status=200)
    app.get('/backoffice/management/form-title/csv', status=200)
    app.get('/backoffice/management/form-title/ods', status=200)
    resp = app.get('/backoffice/management/statistics')
    assert 'cat1' in resp.text


def test_backoffice_multi_actions(pub):
    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text  # always there

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = ['whatever']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text

    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    workflow.store()

    resp = app.get('/backoffice/management/form-title/?limit=20')
    assert 'id="multi-actions"' in resp.text
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True
    resp = resp.forms[0].submit('button-action-1')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    assert (
        resp.pyquery.find('[data-redirect-auto]').attr['href']
        == '/backoffice/management/form-title/?limit=20'
    )
    for id in ids:
        assert formdef.data_class().get(id).status == 'wf-finished'

    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.request.url).query)['job'][0]
    job = MassActionAfterJob.get(job_id)
    assert {str(x) for x in job.processed_ids} == set(ids)

    job.execute()  # run again, it shouldn't run on any item
    assert job.current_count == len(ids)

    # run job while formdata are busy
    for id in ids:
        formdata = formdef.data_class().get(id)
        formdata.status = 'wf-new'
        formdata.workflow_processing_timestamp = datetime.datetime.now()
        formdata.store()

    job.processed_ids = {}
    job.current_count = 0
    job.store()
    job.execute()
    assert set(ids) == set(job.skipped_formdata_ids.keys())
    for id in ids:
        formdata = formdef.data_class().get(id)
        formdata.workflow_processing_timestamp = None
        formdata.store()

    # test doesn't simulate formdata getting back to idle between the too job loops
    # but run again to check the second loop does its job currently.
    assert job.skipped_formdata_ids
    job.execute()
    assert len(job.processed_ids) == 5

    draft_ids = [x.id for x in formdef.data_class().select() if x.status == 'draft']
    resp = app.get('/backoffice/management/form-title/')
    assert resp.forms[0].fields['select[]'][0]._value == '_all'
    resp.forms[0].fields['select[]'][0].checked = True
    resp = resp.forms[0].submit('button-action-1')
    for formdata in formdef.data_class().select():
        if formdata.id in draft_ids:
            assert formdata.status == 'draft'
        else:
            assert formdata.status == 'wf-finished'

    for formdata in formdef.data_class().select():
        if formdata.status != 'draft':
            formdata.jump_status('new')
            formdata.store()

    # action for other role
    action2 = workflow.add_global_action('OTHER ACTION')
    jump = action2.add_action('jump')
    jump.status = 'accepted'
    trigger = action2.triggers[0]
    trigger.roles = ['whatever']
    workflow.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    assert 'OTHER ACTION' not in resp.text

    # action for function
    trigger.roles = ['_foobar']
    workflow.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    assert 'OTHER ACTION' not in resp.text

    workflow.roles['_foobar'] = 'Foobar'
    workflow.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    assert 'OTHER ACTION' in resp.text

    # alter some formdata to simulate dispatch action
    stable_ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        formdata = formdef.data_class().get(checkbox._value)
        formdata.workflow_roles = {'_foobar': [formdef.workflow_roles['_receiver']]}
        formdata.store()
        stable_ids.append(formdata.id)

    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('[data-link="%s/"] input' % stable_ids[0]).attr['data-is__foobar'] == 'true'
    assert 'OTHER ACTION' in resp.text

    resp.forms[0].fields['select[]'][0].checked = True  # _all
    resp = resp.forms[0].submit('button-action-2')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;OTHER ACTION&quot; on forms' in resp.text
    # check only dispatched formdata have been moved by global action executed
    # on all formdatas
    for formdata in formdef.data_class().select():
        if formdata.id in draft_ids:
            assert formdata.status == 'draft'
        elif formdata.id in stable_ids:
            assert formdata.status == 'wf-accepted'
        else:
            assert formdata.status != 'wf-accepted'

    # check webservice (external) triggers are not displayed
    action3 = workflow.add_global_action('THIRD ACTION')
    action3.triggers = []
    trigger = action3.append_trigger('webservice')
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    workflow.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    assert 'THIRD ACTION' not in resp.text

    # check it's possible to hide an action from mass actions
    resp = app.get('/backoffice/management/form-title/')
    assert 'OTHER ACTION' in resp.text
    workflow.global_actions[1].triggers[0].allow_as_mass_action = False
    workflow.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'OTHER ACTION' not in resp.text


def test_backoffice_multi_actions_some_status(pub):
    create_superuser(pub)
    Workflow.wipe()
    FormDef.wipe()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.statuses = ['new']
    trigger.roles = ['_receiver']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()

    initial_statuses = {}
    for i in range(15):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i < 5:
            formdata.jump_status('accepted')
        elif i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        initial_statuses[str(formdata.id)] = formdata.status

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    assert len(resp.pyquery('[data-status_new]')) == 3
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 5

    resp = resp.forms[0].submit('button-action-1')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    new_statuses = {str(x.id): x.status for x in formdef.data_class().select()}
    for id in ids:
        # check action was only executed on "new"
        if initial_statuses[id] == 'wf-new':
            assert new_statuses[id] == 'wf-finished'
        else:
            assert new_statuses[id] == initial_statuses[id]

    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert len(resp.pyquery('[data-status_new]')) == 0
    assert len(resp.pyquery('[data-status_finished]')) == 10
    assert len(resp.pyquery('[data-status_accepted]')) == 5


def test_backoffice_multi_actions_generic_status(pub):
    create_superuser(pub)
    Workflow.wipe()
    FormDef.wipe()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action1 = workflow.add_global_action('FOOBAR')
    register_comment = action1.add_action('register-comment')
    register_comment.comment = 'hello'
    trigger = action1.triggers[0]
    trigger.statuses = ['_endpoint_status']
    trigger.roles = ['_receiver']
    assert set(trigger.get_statuses_ids()) == {'rejected', 'finished'}
    assert trigger.render_as_line() == 'Manual, from final status, by Recipient'

    action2 = workflow.add_global_action('FOOBAR2')
    register_comment = action2.add_action('register-comment')
    register_comment.comment = 'hello2'
    trigger = action2.triggers[0]
    trigger.statuses = ['_waitpoint_status']
    trigger.roles = ['_receiver']
    assert set(trigger.get_statuses_ids()) == {'new', 'accepted'}
    assert trigger.render_as_line() == 'Manual, from pause status, by Recipient'

    action3 = workflow.add_global_action('FOOBAR3')
    register_comment = action3.add_action('register-comment')
    register_comment.comment = 'hello3'
    trigger = action3.triggers[0]
    trigger.statuses = ['_transition_status']
    trigger.roles = ['_receiver']
    assert set(trigger.get_statuses_ids()) == {'just_submitted'}
    assert trigger.render_as_line() == 'Manual, from transition status, by Recipient'

    trigger.statuses = ['_transition_status', 'xxx']  # check with invalid status
    assert trigger.render_as_line() == 'Manual, from transition status, by Recipient'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()

    for i in range(15):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i < 5:
            formdata.jump_status('accepted')
        elif i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    assert len(resp.pyquery('[data-status_new]')) == 3
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 5

    assert resp.pyquery(
        f'form#multi-actions button[name="button-action-{action1.id}"]'
        '[data-visible_status_finished]'
        '[data-visible_status_rejected]'
        ':not([data-visible_status_new])'
        ':not([data-visible_status_accepted])'
        ':not([data-visible_status_just_submitted])'
    )

    assert resp.pyquery(
        f'form#multi-actions button[name="button-action-{action2.id}"]'
        ':not([data-visible_status_finished])'
        ':not([data-visible_status_rejected])'
        '[data-visible_status_new]'
        '[data-visible_status_accepted]'
        ':not([data-visible_status_just_submitted])'
    )

    assert resp.pyquery(
        f'form#multi-actions button[name="button-action-{action3.id}"]'
        ':not([data-visible_status_finished])'
        ':not([data-visible_status_rejected])'
        ':not([data-visible_status_new])'
        ':not([data-visible_status_accepted])'
        '[data-visible_status_just_submitted]'
    )

    resp = resp.forms[0].submit(f'button-action-{action1.id}')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    for id in ids:
        formdata = formdef.data_class().get(id)
        comments = [x.content for x in formdata.iter_evolution_parts(JournalEvolutionPart)]
        # check action was only executed on "finished"
        if formdata.status == 'wf-finished':
            assert comments == ['<p>hello</p>']
        else:
            assert comments == []

    # check not end point status
    resp = app.get('/backoffice/management/form-title/?filter=all')
    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    assert len(resp.pyquery('[data-status_new]')) == 3
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 5
    resp = resp.forms[0].submit(f'button-action-{action2.id}')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR2&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    for id in ids:
        formdata = formdef.data_class().get(id)
        comments = [x.content for x in formdata.iter_evolution_parts(JournalEvolutionPart)]
        # check action was only executed on not final status
        if formdata.status in ('wf-finished', 'wf-rejected'):
            assert comments == ['<p>hello</p>']
        else:
            assert comments == ['<p>hello2</p>']


def test_backoffice_multi_actions_confirmation(pub):
    create_superuser(pub)
    Workflow.wipe()
    FormDef.wipe()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    action.append_trigger('timeout')
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.statuses = ['new']
    trigger.roles = ['_receiver']
    trigger.require_confirmation = True
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('accepted')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert resp.pyquery('#multi-actions button').attr('data-ask-for-confirmation') == 'true'

    trigger.confirmation_text = 'Ok?'
    workflow.store()
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert (
        resp.pyquery('#multi-actions button[ data-ask-for-confirmation]').attr('data-ask-for-confirmation')
        == 'Ok?'
    )


def test_backoffice_multi_actions_jump(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()

    # add identifier to jumps
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.get_status('new').items[1].identifier = 'accept'
    workflow.get_status('new').items[2].identifier = 'reject'
    workflow.get_status('new').items[2].require_confirmation = True
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    initial_statuses = {}
    for i in range(15):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i < 5:
            formdata.jump_status('accepted')
        elif i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        initial_statuses[str(formdata.id)] = formdata.status

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert 'select[]' in resp.forms['multi-actions'].fields
    assert len(resp.pyquery('[data-status_new]')) == 3
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 5
    assert len(resp.pyquery.find('#multi-actions div.buttons button')) == 2
    assert len(resp.pyquery.find('#multi-actions div.buttons button[data-ask-for-confirmation]')) == 1

    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    resp = resp.forms['multi-actions'].submit('button-action-st-new-accept-_accept')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Accept&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    new_statuses = {str(x.id): x.status for x in formdef.data_class().select()}
    for id in ids:
        # check action was only executed on "new"
        if initial_statuses[id] == 'wf-new':
            assert new_statuses[id] == 'wf-accepted'
        else:
            assert new_statuses[id] == initial_statuses[id]

    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert len(resp.pyquery('[data-status_new]')) == 0
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 8

    workflow.get_status('new').items[2].confirmation_text = 'Ok?'
    workflow.store()
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert (
        resp.pyquery('#multi-actions button[data-ask-for-confirmation]').attr('data-ask-for-confirmation')
        == 'Ok?'
    )


def test_backoffice_multi_actions_jump_same_identifier(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()

    # add identifier to jumps
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.get_status('new').items[1].identifier = 'accept'
    workflow.get_status('new').items[2].identifier = 'accept'  # duplicated
    workflow.get_status('new').items[2].require_confirmation = True
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    initial_statuses = {}
    for i in range(15):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i < 5:
            formdata.jump_status('accepted')
        elif i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        initial_statuses[str(formdata.id)] = formdata.status

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert 'select[]' in resp.forms['multi-actions'].fields
    assert len(resp.pyquery('[data-status_new]')) == 3
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 5
    assert len(resp.pyquery.find('#multi-actions div.buttons button')) == 2
    assert len(resp.pyquery.find('#multi-actions div.buttons button[data-ask-for-confirmation]')) == 1

    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    resp = resp.forms['multi-actions'].submit('button-action-st-new-accept-_accept')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Accept&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    new_statuses = {str(x.id): x.status for x in formdef.data_class().select()}
    for id in ids:
        # check action was only executed on "new"
        if initial_statuses[id] == 'wf-new':
            assert new_statuses[id] == 'wf-accepted'
        else:
            assert new_statuses[id] == initial_statuses[id]

    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert len(resp.pyquery('[data-status_new]')) == 0
    assert len(resp.pyquery('[data-status_finished]')) == 7
    assert len(resp.pyquery('[data-status_accepted]')) == 8


def test_backoffice_multi_actions_jump_condition(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    # add identifier to jumps
    accept_button = workflow.get_status('new').items[1]
    accept_button.identifier = 'accept'
    workflow.get_status('new').items[2].identifier = 'reject'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('finished')
        else:
            formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'new'
    resp.forms['listing-settings']['filter-operator'] = 'eq'
    resp = resp.forms['listing-settings'].submit().follow()
    assert 'select[]' in resp.forms['multi-actions'].fields
    assert len(resp.pyquery.find('#multi-actions div.buttons button')) == 2

    checked_ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        checked_ids.append(checkbox._value)
        checkbox.checked = True

    # modify jump to have a condition so it's not executed on some of the checked formdatas
    selected_ids = [checked_ids[1], checked_ids[2]]
    accept_button.condition = {
        'type': 'django',
        'value': 'form_number_raw == "%s" or form_number_raw == "%s"' % tuple(selected_ids),
    }
    workflow.store()

    resp = resp.forms['multi-actions'].submit('button-action-st-new-accept-_accept')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Accept&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    for id in checked_ids:
        if id in selected_ids:
            assert formdef.data_class().get(id).status == 'wf-accepted'
        else:
            assert formdef.data_class().get(id).status == 'wf-new'


@pytest.mark.parametrize('target', [None, 'xxx'])
def test_backoffice_multi_actions_mistarget_jump(pub, target):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()

    # add extra jump, with no target
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    jump = workflow.get_status('new').add_action('choice', id='_test')
    jump.label = 'Test'
    jump.identifier = 'test'
    jump.by = ['_receiver']
    jump.status = target
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert 'select[]' in resp.forms['multi-actions'].fields
    assert len(resp.pyquery.find('#multi-actions div.buttons button')) == 1
    assert resp.pyquery.find('#multi-actions div.buttons button').text() == 'Test'

    ids = []
    for checkbox in resp.forms[0].fields['select[]']:
        if checkbox._value == '_all':
            continue
        # check them all
        ids.append(checkbox._value)
        checkbox.checked = True

    resp = resp.forms['multi-actions'].submit('button-action-st-new-test-_test')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Test&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    # check status didn't change
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.get_status().id == 'new'


def test_backoffice_multi_actions_oldest_form(pub):
    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text  # always there

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('Mark as duplicates')
    jump = action.add_action('jump')
    jump.condition = {'type': 'django', 'value': 'mass_action_index != 0'}
    jump.status = 'rejected'

    jump2 = action.add_action('jump')
    jump2.condition = {'type': 'django', 'value': 'mass_action_index == 0'}
    jump2.status = 'accepted'

    register_comment = workflow.possible_status[2].add_action('register-comment', id='_comment')
    register_comment.comment = '<p>Original form: {{ oldest_form_number }}.</p>'
    assert workflow.possible_status[2].id == 'rejected'

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True
    resp = resp.forms[0].submit('button-action-1')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Mark as duplicates&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    oldest_formdata = None
    for i, id in enumerate(sorted(ids, key=int)):
        if i == 0:
            oldest_formdata = formdef.data_class().get(id)
            assert formdef.data_class().get(id).status == 'wf-accepted'
        else:
            assert formdef.data_class().get(id).status == 'wf-rejected'
            assert (
                formdef.data_class().get(id).evolution[-1].parts[0].content
                == '<p>Original form: %s.</p>' % oldest_formdata.get_display_id()
            )


def test_backoffice_multi_actions_using_session_user(pub):
    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text  # always there

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('Show user')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'session_user={{session_user}}'
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'id="multi-actions"' in resp.text
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True
    resp = resp.forms[0].submit('button-action-1')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;Show user&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    for id in sorted(ids, key=int):
        content = formdef.data_class().get(id).evolution[-1].parts[0].content
        assert 'session_user=admin' in content


def test_backoffice_multi_actions_interactive(pub):
    user = create_superuser(pub)
    LoggedError.wipe()

    formdef = FormDef()
    formdef.name = 'test multi actions interactive'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1, 0, i))
        formdata.jump_status('new')
        formdata.evolution[-1].time = make_aware(datetime.datetime(2015, 1, 1, 0, i))
        formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_url(backoffice=True))
    assert 'id="multi-actions"' in resp.text  # always there

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='test', required='required')
    )
    form_action.hide_submit_button = False
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO {{ session_user }}'

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    resp = app.get(formdef.get_url(backoffice=True))
    assert 'id="multi-actions"' in resp.text
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        checkbox.checked = True
    resp = resp.forms[0].submit('button-action-1')
    assert '/actions/' in resp.location
    resp = resp.follow()
    resp = resp.follow()  # back to form listing
    assert 'Configuration error: no available action.' in resp.text
    assert (
        LoggedError.select()[0].summary
        == 'Configuration error in global interactive action (FOOBAR), check roles and functions.'
    )

    form_action.by = trigger.roles
    workflow.store()

    for check in ('cancel', 'submit'):
        resp = app.get(formdef.get_url(backoffice=True) + '?limit=20')
        ids = []
        for checkbox in resp.forms[0].fields['select[]'][1:6]:
            ids.append(checkbox._value)
            checkbox.checked = True

        resp = resp.forms[0].submit('button-action-1')
        assert '/actions/' in resp.location
        resp = resp.follow()
        assert '5 selected forms' in resp.text
        if check == 'cancel':
            resp = resp.form.submit('cancel')
            assert (
                resp.location
                == 'http://example.net/backoffice/management/test-multi-actions-interactive/?limit=20'
            )
        else:
            # click
            resp = resp.form.submit('submit')
            # and continue to the rest of the test

    assert resp.pyquery('#form_error_fblah_1_1').text() == 'required field'
    resp.form['fblah_1_1'] = 'GLOBAL INTERACTIVE ACTION'
    resp = resp.form.submit('submit')

    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    assert (
        resp.pyquery.find('[data-redirect-auto]').attr['href']
        == '/backoffice/management/test-multi-actions-interactive/?limit=20'
    )
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.request.url).query)['job'][0]
    job = GlobalInteractiveMassActionAfterJob.get(job_id)
    assert {str(x) for x in job.processed_ids} == set(ids)

    job.execute()  # run again, it shouldn't run on any item
    assert job.current_count == len(ids)

    for formdata in formdef.data_class().select():
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        context = pub.substitutions.get_context_variables(mode='lazy')
        if str(formdata.id) in ids:
            assert context['form_workflow_form_blah_var_test'].get_value() == 'GLOBAL INTERACTIVE ACTION'
            history_message = [x for x in formdata.iter_evolution_parts(JournalEvolutionPart)][-1]
            assert 'HELLO admin' in history_message.content
        else:
            with pytest.raises(KeyError):
                assert context['form_workflow_form_blah_var_test']

    # check for various conditions for directory errors
    resp = app.get(formdef.get_url(backoffice=True) + '?limit=20')
    resp.forms[0].fields['select[]'][1].checked = True
    resp = resp.forms[0].submit('button-action-1')
    token = pub.token_class.get(resp.headers['location'].split('/')[-2])
    token.context['form_type'] = 'xxx'
    token.store()
    app.get(resp.headers['location'], status=404)
    token.context['form_type'] = 'formdef'
    token.context['form_ids'] = [formdata.id + 100]  # missing
    token.store()
    app.get(resp.headers['location'], status=404)
    token.context['form_ids'] = [formdata.id]
    token.context['action_id'] = 'xxx'
    token.store()
    app.get(resp.headers['location'], status=404)


def test_backoffice_multi_actions_interactive_create_carddata(pub):
    user = create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    carddef.store()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='test', required='required')
    )
    form_action.hide_submit_button = False

    create_card = action.add_action('create_carddata')
    create_card.label = 'Create Card Data'
    create_card.varname = 'mycard'
    create_card.formdef_slug = carddef.url_name
    create_card.mappings = [
        Mapping(field_id='1', expression='{{ form_var_test }} - {{form_workflow_form_blah_var_test}}'),
    ]

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    form_action.by = trigger.roles
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test multi actions interactive create carddata'
    formdef.fields = [fields.StringField(id='1', label='test', varname='test')]
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.workflow = workflow
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.data['1'] = 'Foo %s' % i
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_url(backoffice=True))
    assert 'id="multi-actions"' in resp.text  # always there

    resp = app.get(formdef.get_url(backoffice=True) + '?limit=20&order_by=id')
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        checkbox.checked = True

    resp = resp.forms[0].submit('button-action-1')
    assert '/actions/' in resp.location
    resp = resp.follow()
    assert '5 selected forms' in resp.text
    resp.form['fblah_1_1'] = 'GLOBAL'
    resp = resp.form.submit('submit')

    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    assert {x.data['1'] for x in carddef.data_class().select()} == {
        'Foo 0 - GLOBAL',
        'Foo 1 - GLOBAL',
        'Foo 2 - GLOBAL',
        'Foo 3 - GLOBAL',
        'Foo 4 - GLOBAL',
    }


@pytest.mark.parametrize('upload_mode', ['no_ajax', 'ajax'])
def test_backoffice_multi_actions_interactive_file_field(pub, upload_mode):
    user = create_superuser(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.FileField(id='1', label='Test', varname='test', required='required')
    )
    form_action.hide_submit_button = False

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    form_action.by = trigger.roles
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test multi actions interactive file field'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.workflow = workflow
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_url(backoffice=True))
    assert 'id="multi-actions"' in resp.text  # always there

    resp = app.get(formdef.get_url(backoffice=True) + '?limit=20&order_by=id')
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True

    resp = resp.forms[0].submit('button-action-1')
    assert '/actions/' in resp.location
    resp = resp.follow()
    assert '5 selected forms' in resp.text
    resp.form['fblah_1_1$file'] = Upload('test3.txt', b'foobar3', 'text/plain')
    if upload_mode == 'ajax':
        # this part is actually done in javascript
        upload_url = resp.form['fblah_1_1$file'].attrs['data-url']
        upload_resp = app.post(upload_url, params=resp.form.submit_fields())
        resp.form['fblah_1_1$file'] = None
        resp.form['fblah_1_1$token'] = upload_resp.json[0]['token']

    resp = resp.form.submit('submit')

    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    for formdata in formdef.data_class().select([Contains('id', ids)]):
        assert (
            formdata.get_substitution_variables()['form'].workflow_form.blah.var.test.raw.get_content()
            == b'foobar3'
        )


@pytest.mark.parametrize('mode', ['no_prefill', 'prefill'])
def test_backoffice_multi_actions_interactive_items_field(pub, mode):
    user = create_superuser(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields = [
        fields.ItemsField(
            id='1', label='Test', varname='test', required='required', items=['foo', 'bar', 'baz']
        )
    ]
    if mode == 'prefill':
        form_action.formdef.fields[0].prefill = {'type': 'string', 'value': 'baz'}

    form_action.hide_submit_button = False

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    form_action.by = trigger.roles
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test multi actions interactive items field'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.workflow = workflow
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_url(backoffice=True))
    assert 'id="multi-actions"' in resp.text  # always there

    resp = app.get(formdef.get_url(backoffice=True) + '?limit=20&order_by=id')
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True

    resp = resp.forms[0].submit('button-action-1')
    assert '/actions/' in resp.location
    resp = resp.follow()
    assert '5 selected forms' in resp.text
    if mode == 'prefill':
        assert resp.form['fblah_1_1$elementbaz'].checked is True
    else:
        assert resp.form['fblah_1_1$elementbaz'].checked is False
    resp.form['fblah_1_1$elementfoo'].checked = True
    resp.form['fblah_1_1$elementbar'].checked = True
    resp.form['fblah_1_1$elementbaz'].checked = False
    resp = resp.form.submit('submit')

    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text

    for formdata in formdef.data_class().select([Contains('id', ids)]):
        assert formdata.get_substitution_variables()['form'].workflow_form.blah.var.test.raw == ['foo', 'bar']


def test_backoffice_multi_actions_abort(pub):
    user = create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    resp = app.get('/backoffice/management/form-title/?limit=20')
    assert 'id="multi-actions"' in resp.text
    ids = []
    for checkbox in resp.forms[0].fields['select[]'][1:6]:
        ids.append(checkbox._value)
        checkbox.checked = True
    resp = resp.forms[0].submit('button-action-1')
    assert '?job=' in resp.location
    resp = resp.follow()
    assert 'Executing task &quot;FOOBAR&quot; on forms' in resp.text
    assert '>completed<' in resp.text
    for id in ids:
        assert formdef.data_class().get(id).status == 'wf-finished'

    job_url = resp.request.url
    resp = app.get(job_url)
    assert not resp.pyquery('#abort-job-button')

    # reset job to mark it as progressing
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.request.url).query)['job'][0]
    job = MassActionAfterJob.get(job_id)
    job.status = 'running'
    job.completion_time = None
    job.processed_ids = {}
    job.current_count = 0
    job.store()

    resp = app.get(job_url)
    assert resp.pyquery('#abort-job-button:not([disabled])')

    job.user_id = 'other'
    job.store()

    # admin can abort all jobs
    resp = app.get(job_url)
    assert resp.pyquery('#abort-job-button:not([disabled])')

    # normal user cannot abort other jobs
    user.is_admin = False
    user.store()
    resp = app.get(job_url)
    assert not resp.pyquery('#abort-job-button')

    # normal user can abort their own jobs
    job.user_id = user.id
    job.store()
    resp = app.get(job_url)
    assert resp.pyquery('#abort-job-button')

    # trigger abort (ajax call)
    resp_ajax = app.post(f'/afterjobs/{job.id}', {'action': 'unknown'})
    assert resp_ajax.json == {'err': 1}
    resp_ajax = app.post(f'/afterjobs/{job.id}', {'action': 'abort'})
    assert resp_ajax.json == {'err': 0}

    job.refresh_column('abort_requested')
    assert job.abort_requested is True

    resp = app.get(job_url)
    assert resp.pyquery('#abort-job-button[disabled]')

    # run again, it should abort
    job.DELAY_BETWEEN_INCREMENT_STORES = 0  # so it gets to increment_count
    job.run()
    assert job.status == 'aborted'


def test_backoffice_map(pub):
    create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # check there's no link to map the sidebar
    assert 'Plot on a Map' not in resp.text

    formdef = FormDef.get_by_urlname('form-title')
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()
    number31.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    number31.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'Plot on a Map' in resp.text
    resp = resp.click('Plot on a Map')
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-geojson-url']
        == 'http://example.net/backoffice/management/form-title/geojson?'
    )
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-tile-urltemplate']
        == 'https://tiles.entrouvert.org/hdm/{z}/{x}/{y}.png'
    )
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-map-attribution']
        == 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    )

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')

    pub.site_options.set('options', 'map-tile-urltemplate', 'https://{s}.tile.example.net/{z}/{x}/{y}.png')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Plot on a Map')
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-tile-urltemplate']
        == 'https://{s}.tile.example.net/{z}/{x}/{y}.png'
    )

    # check query string is kept
    resp = app.get('/backoffice/management/form-title/map?filter=all')
    resp = resp.click('Management view')
    assert resp.request.url.endswith('?filter=all')


def test_backoffice_geojson(pub):
    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields.append(fields.MapField(id='4', label='4th field'))
    formdef.fields.append(fields.MapField(id='5', label='5th field'))
    formdef.store()
    form_class = formdef.data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/geojson', status=404)

    formdef = FormDef.get_by_urlname('form-title')
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()
    number31 = formdef.data_class().get(number31.id)
    number31.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    number31.store()

    resp = app.get('/backoffice/management/form-title/geojson?1=on&4=on&5=on')
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['geometry']['coordinates'] == [2.32, 48.83]
    assert 'status_colour' in resp.json['features'][0]['properties']
    assert resp.json['features'][0]['properties']['status_name'] == 'New'
    assert resp.json['features'][0]['properties']['status_colour'] == '#66FF00'
    assert resp.json['features'][0]['properties']['view_label'] == 'View'
    assert 'display_fields' in resp.json['features'][0]['properties']
    assert len(resp.json['features'][0]['properties']['display_fields']) == 1

    resp = app.get('/backoffice/management/form-title/geojson?filter=pending&filter-status=on')
    assert len(resp.json['features']) == 1

    resp = app.get('/backoffice/management/form-title/geojson?filter=done&filter-status=on')
    assert len(resp.json['features']) == 0

    resp = app.get('/backoffice/management/form-title/geojson?filter=all&filter-status=on')
    assert len(resp.json['features']) == 1
    number31.anonymise()
    resp = app.get('/backoffice/management/form-title/geojson?filter=all&filter-status=on')
    assert len(resp.json['features']) == 0


def test_backoffice_handling(pub):
    create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # check sidebar links are ok
    assert 'Export' in resp.text
    app.get('/backoffice/management/form-title/stats', status=200)
    app.get('/backoffice/management/form-title/csv', status=200)
    app.get('/backoffice/management/form-title/ods', status=200)
    app.get('/backoffice/management/form-title/json', status=200)

    # click on a formdata
    resp = resp.click(href='%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    resp.forms[0]['comment'] = 'HELLO WORLD'
    resp = resp.forms[0].submit('button_accept')
    resp = resp.follow()
    assert FormDef.get_by_urlname('form-title').data_class().get(number31.id).status == 'wf-accepted'
    assert 'HELLO WORLD' in resp.text


def test_backoffice_parallel_handling(pub, freezer):
    create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # open formdata twice
    resp2 = resp.click(href='%s/' % number31.id)
    resp3 = resp.click(href='%s/' % number31.id)

    freezer.move_to(datetime.timedelta(seconds=10))
    resp2.forms[0]['comment'] = 'HELLO WORLD'
    resp2 = resp2.forms[0].submit('button_accept')
    resp2 = resp2.follow()

    resp3.forms[0]['comment'] = 'HELLO WORLD'
    resp3 = resp3.forms[0].submit('button_accept')
    assert resp3.pyquery('.global-errors summary').text() == 'Error: parallel execution.'
    assert (
        resp3.pyquery('.global-errors summary + p').text()
        == 'Another action has been performed on this form in the meantime and data may have been changed.'
    )
    # check it's possible to click it now, as it's been refreshed and the agent warned.
    assert 'button_finish' in resp3.forms[0].fields
    resp3 = resp3.forms[0].submit('button_finish').follow()
    assert not resp3.pyquery('.global-errors summary')


def test_backoffice_handling_global_action(pub):
    create_user(pub)

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')

    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert 'HELLO WORLD GLOBAL ACTION' in resp.text
    assert formdef.data_class().get(formdata.id).status == 'wf-finished'


def test_backoffice_handling_global_action_parallel_handling(pub):
    create_user(pub)

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []

    workflow = Workflow(name='test global action')
    workflow.add_status('st0')
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    resp.form.submit('button-action-1').follow()
    resp3 = resp.form.submit('button-action-1')
    assert resp3.pyquery('.global-errors summary').text() == 'Error: parallel execution.'
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert resp.text.count('HELLO WORLD GLOBAL ACTION') == 1


def test_backoffice_global_remove_action(pub):
    user = create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    action.add_action('remove')
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global remove'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert 'remove' in resp.text
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert resp.request.url == 'http://example.net/backoffice/management/test-global-remove/'
    assert 'The form has been deleted.' in resp.text

    carddef = CardDef()
    carddef.name = 'test global remove'
    carddef.fields = []
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.jump_status('new')
    carddata.user_id = user.id
    carddata.store()

    resp = app.get('/backoffice/data/%s/%s/' % (carddef.url_name, carddata.id))
    assert 'remove' in resp.text
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert resp.request.url == 'http://example.net/backoffice/data/test-global-remove/'
    assert 'The card has been deleted.' in resp.text


def test_backoffice_global_action_jump_to_current_status(pub):
    Workflow.wipe()

    create_user(pub)

    formdef = FormDef()
    formdef.name = 'test jump to current status'
    formdef.fields = []

    workflow = Workflow()
    st1 = workflow.add_status('Status1')
    register_comment = st1.add_action('register-comment', id='_comment')
    register_comment.comment = '<p>WORKFLOW COMMENT</p>'

    action = workflow.add_global_action('FOOBAR')
    action.add_action('jump')
    action.items[0].status = st1.id
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert resp.text.count('WORKFLOW COMMENT') == 1
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert resp.text.count('WORKFLOW COMMENT') == 2


def test_backoffice_global_interactive_action(pub):
    create_user(pub)

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = [
        fields.StringField(id='1', label='1st field', varname='foo'),
    ]
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    display = action.add_action('displaymsg')
    display.message = 'This is a message'
    display.to = []

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.StringField(
            id='1',
            label='Test',
            varname='test',
            required='required',
            prefill={'type': 'string', 'value': 'a{{form_var_foo}}b'},
        ),
    )
    form_action.hide_submit_button = False
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO {{ form_workflow_form_blah_var_test }}'
    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    button1 = action.add_action('choice')
    button1.label = 'button1'
    button1.condition = {'type': 'django', 'value': 'form_var_foo != "plop"'}

    button2 = action.add_action('choice')
    button2.label = 'button2'
    button2.condition = {'type': 'django', 'value': 'form_var_foo == "plop"'}

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'plop'}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()  # -> error, empty action
    resp = resp.follow()  # -> back to form
    assert 'Configuration error: no available action.' in resp.text

    button1.by = trigger.roles
    button2.by = trigger.roles
    form_action.by = trigger.roles
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert 'This is a message' in resp.text
    assert resp.form[f'fblah_{form_action.id}_1'].value == 'aplopb'  # field was prefilled
    resp.form[f'fblah_{form_action.id}_1'] = ''
    assert not resp.pyquery('button[value="button1"]')  # not displayed
    assert resp.pyquery('button[value="button2"]')  # displayed
    resp = resp.form.submit('submit')
    # error as the field was empty
    assert resp.pyquery(f'#form_error_fblah_{form_action.id}_1').text() == 'required field'
    resp.form[f'fblah_{form_action.id}_1'] = 'GLOBAL INTERACTIVE ACTION'
    resp = resp.form.submit('submit')
    assert resp.location == formdata.get_url(backoffice=True)
    resp = resp.follow()

    assert 'HELLO GLOBAL INTERACTIVE ACTION' in resp.text

    # check cancel button
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    resp = resp.form.submit('cancel')
    assert resp.location == formdata.get_url(backoffice=True)


def test_backoffice_global_interactive_action_manual_jump(pub):
    create_user(pub)

    FormDef.wipe()
    Workflow.wipe()
    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    workflow = Workflow(name='test global action jump')
    workflow.add_status('st1')
    st2 = workflow.add_status('st2')

    register_comment = st2.add_action('register-comment')
    register_comment.comment = 'HELLO'

    action = workflow.add_global_action('FOOBAR')

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    button = action.add_action('choice')
    button.label = 'button'
    button.status = str(st2.id)
    button.by = trigger.roles

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    resp = resp.form.submit('button1')
    assert resp.location == formdata.get_url(backoffice=True)
    resp = resp.follow()

    assert 'HELLO' in resp.text


def test_backoffice_global_interactive_action_auto_jump(pub):
    create_user(pub)

    FormDef.wipe()
    Workflow.wipe()
    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    workflow = Workflow(name='test global action jump')
    workflow.add_status('st1')
    st2 = workflow.add_status('st2')

    register_comment = st2.add_action('register-comment')
    register_comment.comment = 'HELLO'

    action = workflow.add_global_action('FOOBAR')

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    commentable = action.add_action('commentable')
    commentable.by = trigger.roles
    commentable.button_label = 'CLICK ME!'

    jump = action.add_action('jump')
    jump.by = trigger.roles
    jump.status = str(st2.id)

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    resp = resp.form.submit('button1')
    assert resp.location == formdata.get_url(backoffice=True)
    resp = resp.follow()

    assert 'HELLO' in resp.text


def test_backoffice_global_interactive_form_with_block(pub):
    create_user(pub)

    BlockDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    workflow = Workflow(name='test global action jump')
    workflow.add_status('st1')

    action = workflow.add_global_action('FOOBAR')

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    form_action = action.add_action('form')
    form_action.by = trigger.roles
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields = [
        fields.BlockField(id='2', label='Blocks', block_slug='foobar', varname='data', max_items='3'),
    ]
    form_action.hide_submit_button = False

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    resp.form[f'fblah_{form_action.id}_2$element0$f123'] = 'foo'
    resp = resp.form.submit(f'fblah_{form_action.id}_2$add_element')
    resp.form[f'fblah_{form_action.id}_2$element1$f123'] = 'foo'
    resp = resp.form.submit('submit').follow()

    formdata.refresh_from_storage()
    part = list(formdata.iter_evolution_parts(WorkflowFormEvolutionPart))[0]
    assert part.data == {
        f'blah_{form_action.id}_2': {'data': [{'123': 'foo'}, {'123': 'foo'}], 'schema': {'123': 'string'}},
        f'blah_{form_action.id}_2_display': 'foobar, foobar',
    }


def test_backoffice_global_interactive_no_live_form(pub):
    create_user(pub)

    FormDef.wipe()
    Workflow.wipe()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    workflow = Workflow(name='test global action jump')
    workflow.add_status('st1')

    action = workflow.add_global_action('FOOBAR')

    trigger = action.triggers[0]
    trigger.roles = [x.id for x in pub.role_class.select() if x.name == 'foobar']

    form_action = action.add_action('form')
    form_action.by = trigger.roles
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields = [
        fields.StringField(id='1', label='Str1', varname='str1'),
        fields.StringField(
            id='2',
            label='Str2',
            varname='str2',
            condition={'type': 'django', 'value': 'form_workflow_form_blah_var_str1 == "test"'},
        ),
    ]
    form_action.hide_submit_button = False

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert 'qommon.forms.js' in resp.text
    assert resp.pyquery('form[data-js-features]:not([data-live-url])')
    resp.form['fblah_1_1'] = 'test'
    assert 'fblah_1_2' not in resp.form.fields  # not in HTML (not hidden with CSS like dynamic fields)
    resp = resp.form.submit('submit').follow()

    formdata.refresh_from_storage()
    part = list(formdata.iter_evolution_parts(WorkflowFormEvolutionPart))[0]
    assert part.data == {'blah_1_1': 'test'}


def test_backoffice_submission_context(pub):
    user = create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # click on a formdata
    resp = resp.click(href='%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text

    # check there's nothing in the sidebar
    assert 'Channel' not in resp.text

    number31.submission_channel = 'mail'
    number31.user_id = user.id
    number31.submission_context = {
        'mail_url': 'http://www.example.com/test.pdf',
        'thumbnail_url': 'http://www.example.com/thumbnail.png',
        'comments': 'test_backoffice_submission_context',
        'summary_url': 'http://www.example.com/summary',
    }
    number31.submission_agent_id = str(user.id)
    number31.store()
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click(href='%s/' % number31.id)
    assert 'Channel' in resp.text
    assert 'http://www.example.com/thumbnail.png' in resp.text
    assert 'http://www.example.com/test.pdf' in resp.text
    assert 'Associated User' in resp.text
    assert 'test_backoffice_submission_context' in resp.text
    assert 'http://www.example.com/summary' in resp.text
    assert 'by %s' % user.get_display_name() in resp.text


def test_backoffice_download_as_zip(pub):
    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields.append(fields.FileField(id='4', label='file1 field'))
    formdef.fields.append(fields.FileField(id='5', label='file2 field'))
    formdef.fields.append(fields.FileField(id='6', label='file3 field'))
    formdef.store()
    number31 = [x for x in formdef.data_class().select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.data['4'] = PicklableUpload('/foo/bar', content_type='text/plain')
    number31.data['4'].receive([b'hello world'])
    number31.data['5'] = PicklableUpload('/foo/bar', content_type='text/plain')
    number31.data['5'].receive([b'hello world2'])
    number31.data['6'] = PicklableUpload('/foo/bar', content_type='text/plain')  # same file
    number31.data['6'].receive([b'hello world2'])
    number31.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'Download all files as .zip' not in resp
    formdef.management_sidebar_items = formdef.get_default_management_sidebar_items()
    formdef.management_sidebar_items.add('download-files')
    formdef.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.click('Download all files as .zip')
    zip_content = io.BytesIO(resp.body)
    with zipfile.ZipFile(zip_content, 'a') as zipf:
        filelist = zipf.namelist()
        assert set(filelist) == {'1_bar', '2_bar'}
        for zipinfo in zipf.infolist():
            content = zipf.read(zipinfo)
            if zipinfo.filename == '1_bar':
                assert content == b'hello world'
            elif zipinfo.filename == '2_bar':
                assert content == b'hello world2'
            else:
                assert False  # unknown zip part


def test_backoffice_sidebar_user_template(pub):
    user = create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.user_id = user.id
    number31.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click(href='%s/' % number31.id)
    assert 'Associated User' in resp.text
    assert '<p>admin</p>' in resp.text
    pub.cfg['users'] = {'sidebar_template': 'XXX{{ form_user_display_name }}YYY'}
    pub.write_cfg()
    resp = app.get(resp.request.url)
    assert '<p>XXXadminYYY</p>' in resp.text
    pub.cfg['users'] = {'sidebar_template': 'XXX<b>{{ form_user_display_name }}</b>YYY'}
    pub.write_cfg()
    resp = app.get(resp.request.url)
    assert '<p>XXX<b>admin</b>YYY</p>' in resp.text

    # check proper escaping
    user.name = 'adm<i>n'
    user.store()
    resp = app.get(resp.request.url)
    assert '<p>XXX<b>adm&lt;i&gt;n</b>YYY</p>' in resp.text

    pub.cfg['users'] = {'sidebar_template': '<b>{{ form_user_display_name }}</b>YYY'}
    pub.write_cfg()
    resp = app.get(resp.request.url)
    assert '<p><b>adm&lt;i&gt;n</b>YYY</p>' in resp.text


def test_backoffice_geolocation_info(pub):
    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # click on a formdata
    resp = resp.click(href='%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text

    # check there's nothing in the sidebar
    assert 'Geolocation' not in resp.text

    number31.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    number31.store()
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click(href='%s/' % number31.id)
    assert 'Geolocafoobar' in resp.text
    assert 'class="qommon-map"' in resp.text
    assert 'data-init-lng="2.32"' in resp.text
    assert 'data-init-lat="48.83' in resp.text


def test_backoffice_sidebar_elements(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    other_formdata = formdef.data_class()()
    other_formdata.just_created()
    other_formdata.just_created()
    other_formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())
    assert [x.text for x in resp.pyquery('#sidebar .extra-context h3')] == [
        'General Information',
        'Associated User',
        'Geolocation',
    ]
    assert len(resp.pyquery('[data-async-url$="/user-pending-forms"]')) == 1

    formdef.management_sidebar_items = ['general', 'pending-forms']
    formdef.store()
    resp = app.get(formdata.get_backoffice_url())
    assert [x.text for x in resp.pyquery('#sidebar .extra-context h3')] == ['General Information']
    assert len(resp.pyquery('[data-async-url$="/user-pending-forms"]')) == 1

    formdef.management_sidebar_items = ['geolocation']
    formdef.store()
    resp = app.get(formdata.get_backoffice_url())
    assert [x.text for x in resp.pyquery('#sidebar .extra-context h3')] == ['Geolocation']
    assert len(resp.pyquery('[data-async-url$="/user-pending-forms"]')) == 0


def test_backoffice_info_text(pub):
    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='info texts')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.button_label = 'CLICK ME!'

    commentable2 = st1.add_action('commentable', id='_commentable2')
    commentable2.by = ['_submitter']
    commentable2.button_label = 'CLICK ME2!'

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'CLICK ME!' in resp.text
    assert 'CLICK ME2!' not in resp.text
    assert 'backoffice-description' not in resp.text

    # add an info text to the status
    st1.backoffice_info_text = '<p>Foo</p>'
    workflow.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'backoffice-description' in resp.text
    assert '<p>Foo</p>' in resp.text

    # add an info text to the button
    commentable.backoffice_info_text = '<p>Bar</p>'
    workflow.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'backoffice-description' in resp.text
    assert '<p>Foo</p>' in resp.text
    assert '<p>Bar</p>' in resp.text

    # info text is not visible if form is locked
    second_user = pub.user_class(name='foobar')
    second_user.roles = pub.role_class.keys()
    second_user.store()
    account = PasswordAccount(id='foobar')
    account.set_password('foobar')
    account.user_id = second_user.id
    account.store()
    app2 = login(get_app(pub), username='foobar', password='foobar')
    resp = app2.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'Be warned forms of this user are also being looked' in resp.text
    assert 'backoffice-description' not in resp.text
    assert 'CLICK ME!' not in resp.text
    assert 'CLICK ME2!' not in resp.text

    # remove info text from the status
    st1.backoffice_info_text = None
    workflow.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'backoffice-description' in resp.text
    assert '<p>Foo</p>' not in resp.text
    assert '<p>Bar</p>' in resp.text

    # add info text to second button
    commentable2.backoffice_info_text = '<p>Baz</p>'
    workflow.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'backoffice-description' in resp.text
    assert '<p>Foo</p>' not in resp.text
    assert '<p>Bar</p>' in resp.text
    assert '<p>Baz</p>' not in resp.text

    # remove info text from first button
    commentable.backoffice_info_text = None
    workflow.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'backoffice-description' not in resp.text


def test_backoffice_handling_post_dispatch(pub):
    # check a formdata that has been dispatched to another role is accessible
    # by an user with that role.
    user1 = create_user(pub)
    role = pub.role_class(name='foobaz')
    role.allows_backoffice_access = True
    role.store()
    user1.roles = [role.id]
    user1.store()
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))

    # check there's no access at the moment
    resp = app.get('/backoffice/management/').follow()
    assert 'form-title/' not in resp.text
    resp = app.get('/backoffice/management/form-title/', status=403)
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id, status=403)

    # emulate a dispatch (setting formdata.workflow_roles), receiver of that
    # formdata is now the local role we gave to the user.
    formdata31 = form_class.get(number31.id)
    formdata31.workflow_roles = {'_receiver': [role.id]}
    formdata31.store()

    # check listing is accessible, with a single item
    resp = app.get('/backoffice/management/').follow()
    assert 'form-title/' in resp.text
    resp = app.get('/backoffice/management/form-title/', status=200)
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 1

    # check statistics and exports are also available
    assert 'Export' in resp.text
    app.get('/backoffice/management/form-title/stats', status=200)
    app.get('/backoffice/management/form-title/csv', status=200)
    app.get('/backoffice/management/form-title/ods', status=200)
    app.get('/backoffice/management/form-title/json', status=200)

    # check formdata is accessible, and that it's possible to perform an action
    # on it.
    resp = resp.click(href='%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    resp.forms[0]['comment'] = 'HELLO WORLD'
    resp = resp.forms[0].submit('button_accept')
    resp = resp.follow()
    assert FormDef.get_by_urlname('form-title').data_class().get(number31.id).status == 'wf-accepted'
    assert 'HELLO WORLD' in resp.text


def test_backoffice_wscall_failure_display(http_requests, pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wscall')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wscall = st1.add_action('webservice_call', id='_wscall')
    wscall.varname = 'xxx'
    wscall.url = 'http://remote.example.net/xml'
    wscall.action_on_bad_data = ':stop'
    wscall.record_errors = True

    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_receiver']
    again.status = st1.id

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Again' in resp.text
    resp = resp.forms[0].submit('button_again')
    resp = resp.follow()
    assert 'Error during webservice call' in resp.text

    number31.user_id = user.id  # change ownership to stay in frontoffice
    number31.store()
    # the failure message shouldn't be displayed in the frontoffice
    resp = app.get('/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Error during webservice call' not in resp.text


@pytest.mark.parametrize('notify_on_errors', [True, False])
@pytest.mark.parametrize('record_on_errors', [True, False])
def test_backoffice_wscall_on_error(http_requests, pub, emails, notify_on_errors, record_on_errors):
    pub.cfg['debug'] = {'error_email': 'errors@localhost.invalid'}
    pub.cfg['emails'] = {'from': 'from@localhost.invalid'}
    pub.write_cfg()

    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wscall')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wscall = st1.add_action('webservice_call', id='_wscall')
    wscall.varname = 'xxx'
    wscall.url = 'http://remote.example.net/xml'
    wscall.action_on_bad_data = ':stop'
    wscall.notify_on_errors = notify_on_errors
    wscall.record_on_errors = record_on_errors
    wscall.record_errors = True

    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_receiver']
    again.status = st1.id

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Again' in resp.text
    resp = resp.forms[0].submit('button_again')
    resp = resp.follow()
    assert 'Error during webservice call' in resp.text

    # check email box
    if notify_on_errors:
        assert emails.count() == 1
        error_email = emails.get(
            '[ERROR] Webservice action: json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)'
        )
        assert '/form-title/%s/' % number31.id in error_email['payload']
        assert error_email['from'] == 'from@localhost.invalid'
        assert error_email['email_rcpt'] == ['errors@localhost.invalid']
        if record_on_errors:
            assert error_email['msg']['References']
    else:
        assert emails.count() == 0

    # check LoggedError
    if record_on_errors:
        assert LoggedError.count() == 1
    else:
        assert LoggedError.count() == 0


def test_backoffice_wscall_attachment(http_requests, pub):
    create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wscall')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]

    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wscall = st1.add_action('webservice_call', id='_wscall')
    wscall.varname = 'xxx'
    wscall.response_type = 'attachment'
    wscall.backoffice_filefield_id = 'bo1'
    wscall.url = 'http://remote.example.net/xml'
    wscall.action_on_bad_data = ':stop'
    wscall.record_errors = True

    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_receiver']
    again.status = st1.id

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Again' in resp.text
    resp = resp.forms[0].submit('button_again')
    resp = resp.follow()

    # get the two generated files from backoffice: in backoffice fields
    # (wscall.backoffice_filefield_id), and in history
    for index in (0, 1):
        resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
        resp = resp.click('xxx.xml', index=index)
        assert resp.location.endswith('/xxx.xml')
        resp = resp.follow()
        assert resp.content_type == 'text/xml'
        assert resp.text == '<?xml version="1.0"><foo/>'

    formdata = formdef.data_class().get(number31.id)
    assert formdata.evolution[-1].parts[0].orig_filename == 'xxx.xml'
    assert formdata.evolution[-1].parts[0].content_type == 'text/xml'
    assert formdata.get_substitution_variables()['attachments'].xxx.filename == 'xxx.xml'
    resp = app.get(formdata.get_substitution_variables()['attachments'].xxx.url)
    resp = resp.follow()
    assert resp.text == '<?xml version="1.0"><foo/>'


def test_backoffice_wfedit(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.submission_channel = 'mail'
    number31.submission_context = {
        'mail_url': 'http://www.example.com/test.pdf',
    }
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert len(form_class().get(number31.id).evolution) == 2  # (just submitted, new)
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert 'http://www.example.com/test.pdf' in resp.text  # make sure sidebar has details
    assert 'Tracking Code' not in resp.text  # make sure it doesn't display a tracking code
    assert resp.form['f1'].value == number31.data['1']
    assert resp.form['f%s' % field1.id].value == number31.data[field1.id]
    assert resp.form['f3'].value == number31.data['3']
    assert 'value="Save Changes"' in resp.text
    resp.form['f%s' % field1.id].value = 'bar'
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert form_class().get(number31.id).data[field1.id] == 'bar'
    assert len(form_class().get(number31.id).evolution) == 3
    assert form_class().get(number31.id).evolution[-1].who == str(user.id)
    number31.store()


def test_backoffice_wfedit_disabled(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.submission_context = {
        'mail_url': 'http://www.example.com/test.pdf',
    }
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    app = login(get_app(pub))

    formdef.disabled = True
    formdef.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    resp.form['f%s' % field1.id].value = 'bar'
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert form_class().get(number31.id).data[field1.id] == 'bar'


def test_backoffice_wfedit_submission(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True

    formdef.fields.insert(0, fields.PageField(id='0', label='1st page'))
    formdef.fields.append(fields.PageField(id='4', label='2nd page'))

    formdef.store()
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.backoffice_submission = True
    number31.store()
    formdata_count = form_class.count()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.form['f1'].value == number31.data['1']
    assert resp.form['f%s' % field1.id].value == number31.data[field1.id]
    assert resp.form['f3'].value == number31.data['3']
    resp.form['f%s' % field1.id].value = 'bar'
    resp = resp.form.submit('submit')
    assert 'value="Save Changes"' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert form_class().get(number31.id).data[field1.id] == 'bar'
    number31.store()
    assert formdata_count == form_class.count()


def test_backoffice_wfedit_and_required_comment(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [user.roles[0]]
    commentable.button_label = 'CLICK ME!'
    commentable.required = True

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    app = login(get_app(pub))

    # check a click goes to edition, not blocked by required comment field.
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    resp.form['f%s' % field1.id].value = 'bar'


def test_backoffice_wfedit_and_backoffice_fields(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(
            id='bo1', label='1st backoffice field', varname='backoffice_blah', required='optional'
        ),
    ]

    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    number31 = form_class().get(number31.id)
    number31.data['bo1'] = 'plop'
    number31.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    resp.form['f%s' % field1.id].value = 'bar'
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert form_class().get(number31.id).data['bo1'] == 'plop'


def test_backoffice_wfedit_and_data_source_with_user_info(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    formdef.fields[2].data_source = {
        'type': 'json',
        'value': 'https://www.example.invalid/?name_id={% firstof form_user_display_name "XXX" %}',
    }
    formdef.store()

    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    number31 = form_class().get(number31.id)
    number31.user_id = user.id
    number31.store()

    app = login(get_app(pub))

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://www.example.invalid/',
            json={'data': [{'id': 'A', 'text': 'hello'}, {'id': 'B', 'text': 'world'}]},
        )

        resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
        resp = resp.form.submit('button_wfedit')
        resp = resp.follow()
        assert len(rsps.calls) == 1
        assert '?name_id=admin' in rsps.calls[-1].request.url
        resp.form['f3'].value = 'A'
        resp = resp.form.submit('submit')
        assert len(rsps.calls) == 2
        assert '?name_id=admin' in rsps.calls[-1].request.url
        resp = resp.follow()


def test_backoffice_wfedit_and_form_status(pub):
    user = create_user(pub)

    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    st2 = workflow.add_status('Status2')
    wfedit = st2.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        fields.CommentField(id='1', label='current status: {{form_status}}'),
        fields.CommentField(id='2', label='previous status: {{form_previous_status}}'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/management/test-form/{formdata.id}/')
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.pyquery('[data-field-id="1"]').text() == 'current status: Status1'
    assert resp.pyquery('[data-field-id="2"]').text() == 'previous status:'

    formdata.jump_status(st2.id)
    formdata.store()

    resp = app.get(f'/backoffice/management/test-form/{formdata.id}/')
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.pyquery('[data-field-id="1"]').text() == 'current status: Status2'
    assert resp.pyquery('[data-field-id="2"]').text() == 'previous status: Status1'


def test_backoffice_wfedit_and_workflow_data(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    formdef.fields[2].data_source = {
        'type': 'json',
        'value': 'https://www.example.invalid/?test={% firstof some_workflow_data "XXX" %}',
    }
    formdef.store()

    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    number31 = form_class().get(number31.id)
    number31.workflow_data = {'some_workflow_data': 'foobar'}
    number31.store()

    app = login(get_app(pub))

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://www.example.invalid/',
            json={'data': [{'id': 'A', 'text': 'hello'}, {'id': 'B', 'text': 'world'}]},
        )

        resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
        resp = resp.form.submit('button_wfedit')
        resp = resp.follow()
        assert len(rsps.calls) == 1
        assert '?test=foobar' in rsps.calls[-1].request.url
        resp.form['f3'].value = 'A'
        resp = resp.form.submit('submit')
        assert len(rsps.calls) == 2
        assert '?test=foobar' in rsps.calls[-1].request.url
        resp = resp.follow()


def test_backoffice_wfedit_and_data_source_with_field_info(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    formdef.fields[0].varname = 'bar'
    formdef.fields[2].data_source = {
        'type': 'json',
        'value': 'https://www.example.invalid/?xxx={{ form_var_bar }}',
    }
    formdef.store()

    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    number31 = form_class().get(number31.id)
    number31.data['3'] = 'EE'
    number31.data['3_display'] = 'EE'
    number31.store()

    app = login(get_app(pub))

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://www.example.invalid/',
            json={'data': [{'id': 'DD', 'text': 'DD'}, {'id': 'EE', 'text': 'EE'}]},
        )

        resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
        resp = resp.form.submit('button_wfedit')
        resp = resp.follow()
        assert len(rsps.calls) == 1
        assert '?xxx=FOO%20BAR%2030' in rsps.calls[-1].request.url
        assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 0
        assert resp.form['f3'].value == 'EE'
        resp.form['f3'].value = 'DD'
        resp = resp.form.submit('submit')
        assert len(rsps.calls) == 2
        assert '?xxx=FOO%20BAR%2030' in rsps.calls[-1].request.url
        resp = resp.follow()


def test_backoffice_wfedit_and_user_selection(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Associated User' not in resp
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()

    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Associated User' in resp
    assert formdef.data_class().get(number31.id).user_id == str(user.id)


def test_backoffice_wfedit_and_user_selection_multi_page(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields.insert(0, fields.PageField(id='0', label='1st page'))
    formdef.fields.append(fields.PageField(id='4', label='2nd page'))
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Associated User' not in resp
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()

    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-user-selection')
    resp = resp.form.submit('submit')  # -> save changes
    resp = resp.follow()
    assert 'Associated User' in resp
    assert formdef.data_class().get(number31.id).user_id == str(user.id)

    number31.store()  # save and lose associated user id

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text
    assert 'Associated User' not in resp
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()

    assert resp.pyquery('.submit-user-selection')
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> save changes
    resp = resp.follow()
    assert 'Associated User' in resp
    assert formdef.data_class().get(number31.id).user_id == str(user.id)


def test_backoffice_wfedit_and_user_selection_role_required(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields.insert(0, fields.PageField(id='0', label='1st page'))
    formdef.fields.append(fields.PageField(id='4', label='2nd page'))
    formdef.roles = user.roles
    formdef.submission_user_association = 'roles'
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.user_id = user.id
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert 'Associated User' in resp
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()

    assert 'Associated User' in resp
    assert not resp.pyquery('.submit-user-selection')
    resp = resp.form.submit('submit')  # -> 2nd page
    assert not resp.pyquery('.errornotice')
    assert 'The form must be associated to an user.' not in resp.text

    resp = resp.form.submit('submit')  # -> save changes


def test_backoffice_wfedit_and_live_condition(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields[0].varname = 'foo'
    formdef.fields[1].condition = {'type': 'django', 'value': 'form_var_foo == "test"'}
    form_class = formdef.data_class()

    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    field1 = formdef.fields[1]

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.form.submit('button_wfedit').follow()
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result'][field1.id]['visible']

    resp.form['f1'].value = 'test'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result'][field1.id]['visible']


def test_backoffice_wfedit_and_prefill_with_user_variable(pub):
    user = create_user(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields[0].varname = 'foo'
    formdef.fields[1].condition = {'type': 'django', 'value': 'form_var_foo == "test"'}
    formdef.fields.append(
        fields.StringField(
            id='100',
            label='user name',
            prefill={'type': 'string', 'value': 'a{{form_user_name}}b'},
        )
    )
    formdef.store()

    form_class = formdef.data_class()

    second_user = pub.user_class(name='foobar')
    second_user.store()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    number31.user_id = second_user.id
    number31.store()

    # attach a custom workflow
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1', number31.status.split('-')[1])

    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    resp = resp.form.submit('button_wfedit').follow()
    assert resp.form['f100'].value == 'afoobarb'

    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url + '?prefilled_100=on', params=resp.form.submit_fields())
    assert live_resp.json['result']['100']['content'] == 'afoobarb'


def test_backoffice_wfedit_and_category(pub):
    user = create_user(pub)
    Category.wipe()

    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    cat1 = Category(name='cat1')
    cat1.store()

    formdef = FormDef()
    formdef.category_id = cat1.id
    formdef.name = 'test_backoffice_wfedit_and_category'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah'}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))

    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.form['f1'].value == 'blah'


def test_backoffice_wfedit_single_page(pub):
    user = create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_receiver']
    editable.operation_mode = 'single'
    editable.page_identifier = 'plop'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.StringField(id='2', label='field1'),
        fields.PageField(id='3', label='2nd page', varname='plop'),
        fields.StringField(id='4', label='field2'),
        fields.StringField(
            id='5', label='field2b', condition={'type': 'django', 'value': 'not is_in_backoffice'}
        ),
        fields.PageField(id='6', label='3rd page'),
        fields.StringField(
            id='7', label='field3', condition={'type': 'django', 'value': 'not is_in_backoffice'}
        ),
    ]
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'2': 'a', '4': 'b', '5': 'b2', '7': 'c'}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))

    resp = app.get(formdata.get_backoffice_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == ['2nd page']
    resp.form['f4'] = 'changed'
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'2': 'a', '4': 'changed', '5': None, '7': 'c'}


def test_backoffice_wfedit_partial_pages(pub):
    user = create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_receiver']
    editable.operation_mode = 'partial'
    editable.page_identifier = 'plop'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.StringField(id='2', label='field1'),
        fields.PageField(id='3', label='2nd page', varname='plop'),
        fields.StringField(id='4', label='field2'),
        fields.StringField(
            id='5', label='field2b', condition={'type': 'django', 'value': 'not is_in_backoffice'}
        ),
        fields.PageField(id='6', label='3rd page'),
        fields.StringField(id='7', label='field3'),
    ]
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'2': 'a', '4': 'b', '5': 'b2', '7': 'c'}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))

    resp = app.get(formdata.get_backoffice_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == ['2nd page', '3rd page']
    resp.form['f4'] = 'changed'
    resp = resp.form.submit('submit')
    resp.form['f7'] = 'changed'
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'2': 'a', '4': 'changed', '5': None, '7': 'changed'}


def test_backoffice_wfedit_and_form_parent(pub):
    user = create_user(pub)
    FormDef.wipe()

    parent_formdef = FormDef()
    parent_formdef.name = 'form for parent'
    parent_formdef.fields = [fields.StringField(id='1', label='field', varname='str')]
    parent_formdef.store()

    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [fields.CommentField(id='1', label='x{{form_parent_form_var_str}}y')]
    formdef.workflow_id = workflow.id
    formdef.store()

    parent_formdata = parent_formdef.data_class()()
    parent_formdata.data = {'1': 'plop'}
    parent_formdata.just_created()
    parent_formdata.store()

    formdata = formdef.data_class()()
    formdata.submission_context = {
        'orig_object_type': 'formdef',
        'orig_formdef_id': parent_formdef.id,
        'orig_formdata_id': parent_formdata.id,
    }
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.pyquery('.comment-field').text() == 'xplopy'


def test_backoffice_wfedit_and_formdata_uuid(pub):
    user = create_user(pub)
    FormDef.wipe()

    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [fields.CommentField(id='1', label='x{{form_uuid}}y')]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    assert resp.pyquery('.comment-field').text() == 'x%sy' % formdata.uuid


def test_global_listing(pub):
    create_user(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/').follow()
    assert 'Global View' in resp.text
    resp = resp.click('Global View')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 20
    assert 'Map View' not in resp.text

    resp = app.get('/backoffice/management/listing?limit=500')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37  # 17 formdef1 + 20 formdef2

    resp = app.get('/backoffice/management/listing?offset=20&limit=20')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 17

    # try an overbound offset
    resp = app.get('/backoffice/management/listing?offset=40&limit=20')
    resp = resp.follow()
    assert resp.forms['listing-settings']['offset'].value == '0'

    resp = app.get('/backoffice/management/listing')
    resp.forms['listing-settings']['end'] = '2014-02-01'
    resp = resp.forms['listing-settings'].submit()

    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 20
    assert 'http://example.net/backoffice/management/other-form/' in resp.text
    assert 'http://example.net/backoffice/management/form-title/' not in resp.text

    formdef = FormDef.get_by_urlname('form-title')
    last_update_time = formdef.data_class().select(lambda x: not x.is_draft())[0].last_update_time
    # check created and last modified columns
    assert '>2014-01-01 00:00<' in resp.text
    assert f'>{last_update_time.strftime("%Y-%m-%d")}' in resp.text

    # check digest is included
    formdata = formdef.data_class().get(
        re.findall(r'data-link="(.*?)"', app.get('/backoffice/management/listing').text)[0].split('/')[-2]
    )
    formdata.formdef.digest_templates = {'default': 'digest of number <{{form_number}}>'}
    formdata.store()
    assert formdata.get(formdata.id).digests['default']
    resp = app.get('/backoffice/management/listing')
    assert formdata.get_url(backoffice=True) in resp.text
    assert 'digest of number &lt;%s&gt;' % formdata.id_display in resp.text

    # check a Channel column is added when not enabled
    assert 'Channel' not in resp.text

    pub.cfg['submission-channels'] = {'include-in-global-listing': True}
    pub.write_cfg()

    resp = app.get('/backoffice/management/listing?limit=500')
    formdata = formdef.data_class().select(lambda x: x.status == 'wf-new')[0]
    formdata.submission_channel = 'mail'
    formdata.store()
    assert 'Channel' in resp.text
    assert '>Web<' in resp.text
    resp.forms['listing-settings']['submission_channel'] = 'web'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 36
    resp.forms['listing-settings']['submission_channel'] = 'mail'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 1

    resp = app.get('/backoffice/management/listing?limit=500')
    resp.forms['listing-settings']['q'] = 'foo'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 17

    resp = app.get('/backoffice/management/listing?limit=500')
    resp.forms['listing-settings']['status'] = 'waiting'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37
    resp.forms['listing-settings']['status'] = 'open'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37
    resp.forms['listing-settings']['status'] = 'all'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 70
    resp.forms['listing-settings']['status'] = 'done'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 33

    # change role handling a formdef, make sure they do not appear anylonger in
    # the all/done views.
    role = pub.role_class(name='whatever')
    role.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().rebuild_security()

    resp = app.get('/backoffice/management/listing?limit=500')
    resp.forms['listing-settings']['status'] = 'waiting'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 20
    assert 'form-title' not in resp.text
    resp.forms['listing-settings']['status'] = 'open'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 20
    assert 'form-title' not in resp.text
    resp.forms['listing-settings']['status'] = 'all'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 20
    assert 'form-title' not in resp.text
    resp.forms['listing-settings']['status'] = 'done'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 0
    assert 'form-title' not in resp.text


def test_global_listing_parameters_from_query_string(pub):
    create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing')
    assert resp.forms['listing-settings']['status'].value == 'waiting'
    assert resp.forms['listing-settings']['limit'].value == '20'

    resp = app.get('/backoffice/management/listing?status=done')
    assert resp.forms['listing-settings']['status'].value == 'done'
    assert resp.forms['listing-settings']['limit'].value == '20'

    resp = app.get('/backoffice/management/listing?status=done&limit=50')
    assert resp.forms['listing-settings']['status'].value == 'done'
    assert resp.forms['listing-settings']['limit'].value == '50'

    resp = app.get('/backoffice/management/listing?status=done&limit=50&q=test')
    assert resp.forms['listing-settings']['status'].value == 'done'
    assert resp.forms['listing-settings']['limit'].value == '50'
    assert resp.forms['listing-settings']['q'].value == 'test'


@pytest.mark.parametrize('settings_mode', ['new', 'legacy'])
def test_global_listing_user_label(pub, settings_mode):
    create_user(pub)
    FormDef.wipe()

    from wcs.admin.settings import UserFieldsFormDef

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='3', label='first_name', varname='first_name'))
    user_formdef.fields.append(fields.StringField(id='4', label='last_name', varname='last_name'))
    user_formdef.store()
    if settings_mode == 'new':
        pub.cfg['users']['fullname_template'] = '{{ user_var_first_name }} {{ user_var_last_name }}'
    else:
        pub.cfg['users']['field_name'] = ['3', '4']
    pub.write_cfg()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.fields = [
        fields.StringField(id='1', label='first_name', prefill={'type': 'user', 'value': '3'}),
        fields.StringField(id='2', label='last_name', prefill={'type': 'user', 'value': '4'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah', '2': 'xxx'}
    formdata.just_created()
    formdata.store()
    formdata.jump_status('new')

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/').follow()
    resp = resp.click('Global View')
    assert '<td class="cell-user">blah xxx</td>' in resp.text


def test_global_listing_back_to_listing_links(pub):
    user = create_user(pub)
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.jump_status('new')

    app = login(get_app(pub))
    # check "back to listing" links when coming from global listing
    resp = app.get('/backoffice/management/listing')
    url = resp.pyquery('tbody tr a').attr.href
    assert resp.pyquery('tbody tr').attr['data-link'] == url
    resp = app.get(url)
    assert resp.pyquery('#formdata-bottom-links a').attr.href == '/backoffice/management/listing'
    assert resp.pyquery('#back-to-listing').attr.href == '/backoffice/management/listing'
    resp = resp.form.submit('button_accept').follow()
    assert resp.pyquery('#formdata-bottom-links a').attr.href == '/backoffice/management/listing'
    assert resp.pyquery('#back-to-listing').attr.href == '/backoffice/management/listing'

    # and when not
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('#formdata-bottom-links a').attr.href == '..'
    assert resp.pyquery('#back-to-listing').attr.href == '..'
    resp = resp.form.submit('button_finish').follow()
    assert resp.pyquery('#formdata-bottom-links a').attr.href == '..'
    assert resp.pyquery('#back-to-listing').attr.href == '..'


def test_management_views_with_no_formdefs(pub):
    create_user(pub)
    FormDef.wipe()

    from wcs.sql import drop_global_views, get_connection_and_cursor

    conn, cur = get_connection_and_cursor()
    drop_global_views(conn, cur)
    cur.close()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert 'This site is currently empty.' in resp.text
    resp = app.get('/backoffice/management/listing')
    assert 'This site is currently empty.' in resp.text


def test_categories_in_forms_listing(pub):
    FormDef.wipe()
    Category.wipe()

    user = create_user(pub)
    user.preferences = {}
    user.store()

    cat1 = Category(name='cat1')
    cat1.position = 1
    cat1.store()
    cat2 = Category(name='cat2')
    cat2.position = 2
    cat2.store()

    for i in range(5):
        formdef = FormDef()
        formdef.name = f'form {i}'
        formdef.workflow_roles = {'_receiver': user.roles[0]}
        if i > 2:
            formdef.category_id = cat1.id
        elif i > 0:
            formdef.category_id = cat2.id
        formdef.store()
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert [x.text for x in resp.pyquery('.section h3')] == ['cat1', 'cat2', 'Misc']
    assert resp.pyquery('.foldable:not(.folded)').length == 2

    pref_name = resp.pyquery('.foldable:not(.folded)')[0].attrib['data-section-folded-pref-name']

    # fold first category
    app.post_json('/api/user/preferences', {pref_name: True}, status=200)
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('.foldable:not(.folded)').length == 1
    assert resp.pyquery('.foldable.folded').length == 1
    assert resp.pyquery('.foldable.folded')[0].attrib['data-section-folded-pref-name'] == pref_name


def test_category_in_global_listing(pub):
    FormDef.wipe()
    Category.wipe()

    create_user(pub)

    formdef = FormDef()
    formdef.name = 'form-3'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing?limit=500')
    assert 'category_ids$element0' not in resp.forms['listing-settings'].fields

    cat1 = Category(name='cat1')
    cat1.position = 1
    cat1.store()
    formdef = FormDef()
    formdef.name = 'form-1'
    formdef.category_id = cat1.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    cat2 = Category(name='cat2')
    cat1.position = 2
    cat2.store()
    formdef = FormDef()
    formdef.name = 'form-2'
    formdef.category_id = cat2.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    resp = app.get('/backoffice/management/listing')
    assert 'category_ids$element0' in resp.forms['listing-settings'].fields
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' in resp.text
    assert 'management/form-3/' in resp.text

    resp.forms['listing-settings']['category_ids$element0'] = cat1.id
    resp = resp.forms['listing-settings'].submit()
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' not in resp.text
    assert 'management/form-3/' not in resp.text

    resp.forms['listing-settings']['category_ids$element0'] = cat2.id
    resp = resp.forms['listing-settings'].submit()
    assert 'management/form-1/' not in resp.text
    assert 'management/form-2/' in resp.text
    assert 'management/form-3/' not in resp.text

    resp = resp.forms['listing-settings'].submit('category_ids$add_element')
    resp.forms['listing-settings']['category_ids$element0'] = cat1.id
    resp.forms['listing-settings']['category_ids$element1'] = cat2.id
    resp = resp.forms['listing-settings'].submit()
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' in resp.text
    assert 'management/form-3/' not in resp.text

    resp = app.get('/backoffice/management/listing?category_slugs=cat1')
    assert resp.forms['listing-settings']['category_ids$element0'].value == str(cat1.id)
    assert 'category_ids$element1' not in resp.forms['listing-settings'].fields
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' not in resp.text
    assert 'management/form-3/' not in resp.text

    resp = app.get('/backoffice/management/listing?category_slugs=cat1,cat2')
    assert resp.forms['listing-settings']['category_ids$element0'].value == str(cat1.id)
    assert resp.forms['listing-settings']['category_ids$element1'].value == str(cat2.id)
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' in resp.text
    assert 'management/form-3/' not in resp.text

    resp = app.get('/backoffice/management/listing?category_ids$element0=foo')
    assert 'category_ids$element0' in resp.forms['listing-settings'].fields
    assert 'management/form-1/' in resp.text
    assert 'management/form-2/' in resp.text
    assert 'management/form-3/' in resp.text


def test_datetime_in_global_listing(pub):
    create_user(pub)
    create_environment(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing?limit=500')
    resp.forms['listing-settings']['end'] = '01/01/2010'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 0

    resp = app.get('/backoffice/management/listing?limit=500')
    resp.forms['listing-settings']['start'] = '01/01/2010'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37

    resp.forms['listing-settings']['start'] = '01/01/2016'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 0

    resp.forms['listing-settings']['start'] = '01/01/16'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 0

    resp.forms['listing-settings']['start'] = '01/01/10'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37

    resp.forms['listing-settings']['end'] = '01/01/10'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 0

    # check invalid values are simply ignored
    resp.forms['listing-settings']['end'] = 'whatever'
    resp = resp.forms['listing-settings'].submit()
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 37


def test_global_listing_anonymised(pub):
    create_user(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing?limit=500&status=all')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 70

    formdef = FormDef.get_by_urlname('other-form')
    for formdata in formdef.data_class().select():
        formdata.anonymise()

    resp = app.get('/backoffice/management/listing?limit=500&status=all')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 50

    resp = app.get('/backoffice/management/listing?limit=500&status=open')
    assert resp.text[resp.text.index('<tbody') :].count('<tr') == 17


def test_global_listing_geojson(pub):
    create_user(pub)
    create_environment(pub)

    formdef = FormDef.get_by_urlname('form-title')
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()
    for formdata in formdef.data_class().select():
        formdata.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/geojson')
    assert len(resp.json['features']) == 17
    assert resp.json['features'][0]['geometry']['coordinates'] == [2.32, 48.83]
    for feature in resp.json['features']:
        assert feature['properties']['status_colour'] == '#66FF00'
        assert feature['properties']['view_label'] == 'View'
        assert feature['properties']['status_name'] == 'New'
        assert feature['properties']['display_fields']
        assert feature['properties']['display_fields'][0]['label'] == 'Name'
        assert feature['properties']['display_fields'][0]['value'].startswith('form title #')

    resp = app.get('/backoffice/management/geojson?q=aa')
    assert len(resp.json['features']) == 5
    resp = app.get('/backoffice/management/geojson?q=bb')
    assert len(resp.json['features']) == 4
    resp = app.get('/backoffice/management/geojson?q=cc')
    assert len(resp.json['features']) == 8

    resp = app.get('/backoffice/management/geojson')
    assert len(resp.json['features']) == 17
    formdata = formdef.data_class().get(resp.json['features'][0]['properties']['id'].split('-')[1])
    formdata.anonymise()
    resp = app.get('/backoffice/management/geojson')
    assert len(resp.json['features']) == 16


def test_global_map(pub):
    create_user(pub)
    create_environment(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing')
    assert 'Map View' in resp.text
    resp = app.get('/backoffice/management/forms')
    assert 'Map View' in resp.text
    resp = resp.click('Map View')
    assert re.findall(r'data-geojson-url="(.*?)"', resp.text) == [
        'http://example.net/backoffice/management/geojson?'
    ]

    resp = app.get('/backoffice/management/map?q=test')
    assert re.findall(r'data-geojson-url="(.*?)"', resp.text) == [
        'http://example.net/backoffice/management/geojson?q=test'
    ]

    # check filters are kept
    resp = app.get('/backoffice/management/listing')
    resp.forms['listing-settings']['status'] = 'all'
    resp = resp.forms['listing-settings'].submit()
    resp = resp.click('Map View')
    assert resp.forms['listing-settings']['status'].value == 'all'


def test_formdata_lookup(pub):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.enable_tracking_codes = True
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.store()

    formdata3 = formdef.data_class()()
    formdata3.status = 'draft'
    formdata3.store()

    code = TrackingCode()
    code.formdata = formdata

    code2 = TrackingCode()
    code2.formdata = formdata3

    formdata4 = formdef.data_class()()
    formdata4.just_created()
    formdata4.store()
    code3 = TrackingCode()
    code3.formdata = formdata4
    formdata4.anonymise()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/').follow()
    assert 'id="lookup-box"' in resp.text
    resp.forms[0]['query'] = formdata.tracking_code
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata.id
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert 'This form has been accessed via its tracking code' in resp.text

    # check there's no access to other formdata
    app.get('http://example.net/backoffice/management/form-title/%s/' % formdata2.id, status=403)

    # check looking up a formdata id
    resp = app.get('/backoffice/management/').follow()
    resp.form['query'] = formdata.get_display_id()
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata.id

    # check looking up a draft formdata
    resp = app.get('/backoffice/management/').follow()
    resp.form['query'] = formdata3.get_display_id()
    resp = resp.form.submit().follow()
    assert 'This identifier matches a draft form, it is not yet available for management.' in resp.text

    resp.form['query'] = formdata3.tracking_code
    resp = resp.form.submit().follow()
    assert 'This tracking code matches a draft form, it is not yet available for management.' in resp.text

    # check looking up an anonymised formdata
    resp.form['query'] = code3.id
    resp = resp.form.submit().follow()
    assert resp.pyquery('.error').text() == 'No such tracking code or identifier.'

    # check looking up on a custom display_id
    formdata.id_display = '999999'
    formdata.store()
    assert formdata.get_display_id() == '999999'
    resp = app.get('/backoffice/management/').follow()
    resp.form['query'] = formdata.get_display_id()
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata.id

    # try it from the global listing
    resp = app.get('/backoffice/management/listing')
    assert 'id="lookup-box"' in resp.text
    resp.forms[0]['query'] = formdata.tracking_code
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata.id
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    # check redirection on errors
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')

    for value in ('false', 'true'):
        pub.site_options.set('options', 'default-to-global-view', value)
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)

        resp = app.get('/backoffice/management/listing')
        resp.forms[0]['query'] = 'AAAAAAAA'
        resp = resp.forms[0].submit()
        assert resp.location == 'http://example.net/backoffice/management/listing'
        resp = resp.follow()
        assert 'No such tracking code or identifier.' in resp.text

        resp = app.get('/backoffice/management/forms')
        resp.forms[0]['query'] = 'AAAAAAAA'
        resp = resp.forms[0].submit()
        assert resp.location == 'http://example.net/backoffice/management/forms'
        resp = resp.follow()
        assert 'No such tracking code or identifier.' in resp.text

    # check it's not possible to replace back value with anything else
    for invalid_value in ('http://example.invalid/', 'xxx'):
        resp = app.get('/backoffice/management/listing')
        resp.forms[0]['back'] = invalid_value
        resp = resp.forms[0].submit()
        assert resp.location == 'http://example.net/backoffice/management/'


def test_backoffice_sidebar_user_context(pub):
    user = create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert re.findall(r'<tbody.*\/tbody>', resp.text, re.DOTALL)[0].count('<tr') == 17

    # click on a formdata
    resp = resp.click(href='%s/' % number31.id)
    assert (' with the number %s.' % number31.get_display_id()) in resp.text

    # check there's nothing in the sidebar
    assert '/user-pending-forms' not in resp.text

    number31.formdef.digest_templates = {'default': 'digest of number {{form_number}}'}
    number31.user_id = user.id
    number31.store()
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert '/user-pending-forms' in resp.text
    user_pending_form_url = re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0]
    partial_resp = app.get(user_pending_form_url)
    assert number31.get_url(backoffice=True) not in partial_resp.text
    assert number31.digests['default'] in partial_resp.text
    assert '<span class="formname">%s</span>' % number31.formdef.name in partial_resp.text

    # another item with status = new
    number34 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 33'][0]
    number34.user_id = user.id
    number34.store()

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert '/user-pending-forms' in resp.text
    user_pending_form_url = re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0]
    partial_resp = app.get(user_pending_form_url)
    assert number31.get_url(backoffice=True) not in partial_resp.text
    assert number34.get_url(backoffice=True) in partial_resp.text

    cat1 = Category(name='cat1')
    cat1.store()

    formdef = FormDef.get_by_urlname('other-form')
    formdef.category_id = cat1.id
    formdef.store()
    other_formdata = formdef.data_class().select()[0]
    other_formdata.user_id = user.id
    other_formdata.store()

    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert '/user-pending-forms' in resp.text
    user_pending_form_url = re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0]
    partial_resp = app.get(user_pending_form_url)
    assert number34.get_url(backoffice=True) in partial_resp.text
    assert other_formdata.get_url(backoffice=True) in partial_resp.text
    # categories are displayed, and current formdata category is on top
    assert '>cat1<' in partial_resp.text
    assert '>Misc<' in partial_resp.text
    assert partial_resp.text.index('>Misc<') < partial_resp.text.index('>cat1<')


def test_backoffice_sidebar_lateral_block(pub):
    create_user(pub)
    FormDef.wipe()
    Workflow.wipe()

    wf = Workflow(name='WF')
    wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow = wf
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': 'bouh'}
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    assert '/lateral-block' not in resp.text

    formdef.lateral_template = 'XX{{ form_var_string }}XX'
    formdef.store()
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    assert '/lateral-block' in resp.text

    lateral_block_url = re.findall('data-async-url="(.*/lateral-block)"', resp.text)[0]
    partial_resp = app.get(lateral_block_url)
    assert partial_resp.text == '<div class="lateral-block">XXbouhXX</div>'

    # error in lateral template
    formdef.lateral_template = 'XX{% for %}XX'
    formdef.store()

    LoggedError.wipe()
    partial_resp = app.get(lateral_block_url)
    assert partial_resp.text == ''
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == "Could not render lateral template (syntax error in Django template: 'for' statements should have at least four words: for)"
    )


def test_count_open(pub):
    create_user(pub)

    FormDef.wipe()
    resp = login(get_app(pub)).get('/backoffice/management/count')
    assert resp.json['count'] == 0

    create_environment(pub)
    resp = login(get_app(pub)).get('/backoffice/management/count')
    assert resp.json['count'] == 37

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_roles = {'_receiver': 2}  # role the user doesn't have
    formdef.store()
    formdef.data_class().rebuild_security()
    resp = login(get_app(pub)).get('/backoffice/management/count')
    assert resp.json['count'] == 20

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_roles = {'_receiver': 2, '_foobar': 1}
    formdef.store()
    formdef.data_class().rebuild_security()
    resp = login(get_app(pub)).get('/backoffice/management/count')
    assert resp.json['count'] == 20
    resp = login(get_app(pub)).get('/backoffice/management/count?waiting=yes')
    assert resp.json['count'] == 20

    formdef = FormDef.get_by_urlname('form-title')
    workflow = Workflow.get_default_workflow()
    workflow.roles['_foobar'] = 'Foobar'
    workflow.id = '2'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 2, '_foobar': '1'}
    formdef.store()
    formdef.data_class().rebuild_security()
    resp = login(get_app(pub)).get('/backoffice/management/count?waiting=no')
    assert resp.json['count'] == 37
    resp = login(get_app(pub)).get('/backoffice/management/count?waiting=yes')
    assert resp.json['count'] == 20
    resp = login(get_app(pub)).get('/backoffice/management/count')
    assert resp.json['count'] == 20

    # check the callback parameter is ignored, that we still get the default
    # criterias when it's set.
    resp = login(get_app(pub)).get('/backoffice/management/count?callback=toto')
    assert '20' in resp.text


def test_count_backoffice_drafts(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    for i in range(3):
        formdata = formdef.data_class()()
        formdata.data = {'1': 'foo'}
        formdata.just_created()
        formdata.store()

    resp = login(get_app(pub)).get('/backoffice/submission/count')
    assert resp.json['count'] == 0

    formdata1, formdata2, formdata3 = formdef.data_class().select()
    for formdata in (formdata1, formdata2, formdata3):
        formdata.status = 'draft'
        formdata.store()

    resp = login(get_app(pub)).get('/backoffice/submission/count')
    assert resp.json['count'] == 0

    for formdata in (formdata1, formdata2, formdata3):
        formdata.backoffice_submission = True
        formdata.store()

    resp = login(get_app(pub)).get('/backoffice/submission/count')
    assert resp.json['count'] == 3

    formdata1.data = {}
    formdata1.store()

    resp = login(get_app(pub)).get('/backoffice/submission/count?mode=empty')
    assert resp.json['count'] == 1
    resp = login(get_app(pub)).get('/backoffice/submission/count?mode=existing')
    assert resp.json['count'] == 2


def test_menu_json(pub):
    FormDef.wipe()
    create_user(pub)
    resp = login(get_app(pub)).get('/backoffice/menu.json')
    menu_json_str = resp.text
    assert len(resp.json) == 1
    assert resp.json[0]['slug'] == 'management'
    assert resp.headers['content-type'] == 'application/json'

    resp = login(get_app(pub)).get('/backoffice/menu.json?jsonpCallback=foo')
    assert resp.text == 'foo(%s);' % menu_json_str
    assert resp.headers['content-type'] == 'application/javascript'


def test_backoffice_resume_folded(pub):
    create_user(pub)
    create_environment(pub)
    form_class = FormDef.get_by_urlname('form-title').data_class()
    number31 = [x for x in form_class.select() if x.data['1'] == 'FOO BAR 30'][0]
    app = login(get_app(pub))

    # first access: summary is not folded
    resp = app.get('/backoffice/management/form-title/%s/' % number31.id)
    assert resp.pyquery('#summary.section.foldable:not(.folded)')
    # do something: summary is folded
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()
    assert resp.pyquery('#summary.section.foldable.folded')


def test_backoffice_backoffice_submission_in_listings(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    first_link = re.findall(r'data-link="(\d+)/?"', resp.text)[0]
    assert 'backoffice-submission' not in resp.text

    formdata = FormDef.get_by_urlname('form-title').data_class().get(first_link)
    formdata.backoffice_submission = True
    formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    assert 'backoffice-submission' in resp.text


def test_backoffice_backoffice_submission_in_global_listing(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing?limit=100')
    assert 'backoffice-submission' not in resp.text

    formdef = FormDef.get_by_urlname('form-title')
    formdata = formdef.data_class().get(
        re.findall(r'data-link="(.*?)"', app.get('/backoffice/management/listing').text)[0].split('/')[-2]
    )
    formdata.backoffice_submission = True
    formdata.store()

    resp = app.get('/backoffice/management/listing?limit=100')
    assert 'backoffice-submission' in resp.text


def test_backoffice_advisory_lock(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    second_user = pub.user_class(name='foobar')
    second_user.roles = pub.role_class.keys()
    second_user.store()
    account = PasswordAccount(id='foobar')
    account.set_password('foobar')
    account.user_id = second_user.id
    account.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    first_link = re.findall(r'data-link="(\d+/)?"', resp.text)[0]
    assert 'advisory-lock' not in resp.text

    app2 = login(get_app(pub), username='foobar', password='foobar')
    resp = app2.get('/backoffice/management/form-title/')
    assert 'advisory-lock' not in resp.text

    resp = app.get('/backoffice/management/form-title/' + first_link)
    resp = app2.get('/backoffice/management/form-title/')
    assert 'advisory-lock' in resp.text
    resp = app.get('/backoffice/management/form-title/')
    assert 'advisory-lock' not in resp.text

    # check global view
    resp = app2.get('/backoffice/management/listing?limit=100')
    assert 'advisory-lock' in resp.text
    resp = app.get('/backoffice/management/listing?limit=100')
    assert 'advisory-lock' not in resp.text

    resp = app.get('/backoffice/management/form-title/' + first_link)
    assert 'Be warned forms of this user are also being looked' not in resp.text
    assert 'button_commentable' in resp.text
    assert len(resp.forms)
    resp = app2.get('/backoffice/management/form-title/' + first_link)
    assert 'Be warned forms of this user are also being looked' in resp.text
    assert 'button_commentable' not in resp.text
    assert len(resp.forms) == 0
    # revisit with first user, no change
    resp = app.get('/backoffice/management/form-title/' + first_link)
    assert 'Be warned forms of this user are also being looked' not in resp.text
    assert 'button_commentable' in resp.text
    # back to second
    resp = app2.get('/backoffice/management/form-title/' + first_link)
    assert 'Be warned forms of this user are also being looked' in resp.text
    assert 'button_commentable' not in resp.text
    resp = resp.click('(unlock actions)')
    resp = resp.follow()
    assert 'Be warned forms of this user are also being looked' in resp.text
    assert 'button_commentable' in resp.text
    assert '(unlock actions)' not in resp.text
    assert len(resp.forms)

    # submit action form
    resp.form['comment'] = 'HELLO'
    resp = resp.form.submit('button_commentable')
    # locks are reset after an action
    assert 'advisory-lock' not in app2.get('/backoffice/management/form-title/')
    assert 'advisory-lock' not in app.get('/backoffice/management/form-title/')
    # but as the current user is redirected to the form, a lock will be
    # acquired (unless the user didn't have actions anymore, but it's not the
    # case here)
    resp = resp.follow()
    assert 'advisory-lock' not in app2.get('/backoffice/management/form-title/')
    assert 'advisory-lock' in app.get('/backoffice/management/form-title/')

    # don't lock a form on removed users
    second_user.remove_self()
    assert 'advisory-lock' in app.get('/backoffice/management/form-title/')  # still marked in listing
    resp = app.get('/backoffice/management/form-title/' + first_link)
    assert 'Be warned forms of this user are also being looked' not in resp.text  # but not on view
    assert resp.forms['wf-actions']  # and the action form is available


def test_backoffice_advisory_lock_related_formdatas(pub):
    pub.session_manager.session_class.wipe()
    user = create_superuser(pub)
    create_environment(pub)

    formdef = FormDef.get_by_urlname('form-title')
    formdatas = formdef.data_class().select(lambda x: x.status == 'wf-new')

    second_user = pub.user_class(name='foobar')
    second_user.roles = pub.role_class.keys()
    second_user.store()
    account = PasswordAccount(id='foobar')
    account.set_password('foobar')
    account.user_id = second_user.id
    account.store()

    third_user = pub.user_class(name='user')
    third_user.store()

    for formdata in formdatas[:2]:
        formdata.user_id = third_user.id
        formdata.store()

    second_formdef = FormDef.get_by_urlname('other-form')
    second_formdef.workflow_roles = {}
    second_formdef.store()
    other_formdatas = second_formdef.data_class().select(lambda x: x.status == 'wf-new')
    other_formdatas[0].user_id = third_user.id
    other_formdatas[0].store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdatas[0].id)
    assert 'Be warned forms of this user are also being looked' not in resp.text
    app.get(re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0])

    app2 = login(get_app(pub), username='foobar', password='foobar')
    resp2 = app2.get('/backoffice/management/form-title/%s/' % formdatas[0].id)
    assert 'Be warned forms of this user are also being looked' in resp2.text
    app2.get(re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0])

    # another by the same user
    resp2 = app2.get('/backoffice/management/form-title/%s/' % formdatas[1].id)
    assert 'Be warned forms of this user are also being looked' in resp2.text
    app2.get(re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0])

    # another by another user
    resp2 = app2.get('/backoffice/management/form-title/%s/' % formdatas[3].id)
    assert 'Be warned forms of this user are also being looked' not in resp2.text
    app2.get(re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0])

    # check another formdef is only marked as visited if the user has potential
    # actions on it.
    session = pub.session_manager.session_class.select(lambda x: x.user == user.id)[0]
    second_formdef.workflow_roles = {'_receiver': 1}
    second_formdef.store()
    other_formdata = second_formdef.data_class().get(other_formdatas[0].id)
    other_formdata.store()  # update concerned_roles

    assert 'formdata-other-form-%d' % other_formdata.id not in session.visiting_objects.keys()
    session.visiting_objects = {}
    session.store()

    resp = app.get('/backoffice/management/form-title/%s/' % formdatas[0].id)
    app.get(re.findall('data-async-url="(.*/user-pending-forms)"', resp.text)[0])
    session = pub.session_manager.session_class.select(lambda x: x.user == user.id)[0]
    assert 'formdef-other-form-%d' % other_formdata.id in session.visiting_objects.keys()


def test_backoffice_resubmit(pub):
    user = create_user(pub)

    wf = Workflow(name='resubmit')
    st1 = wf.add_status('Status1')
    st2 = wf.add_status('Status2')

    resubmit = st1.add_action('resubmit', id='_resubmit')
    resubmit.by = [user.roles[0]]

    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id

    register_comment = st2.add_action('register-comment', id='_register')
    register_comment.comment = '<p><a href="[resubmit_formdata_backoffice_url]">resubmitted</a></p>'

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field', varname='foo'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = wf.id
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'form title bis'
    formdef2.backoffice_submission_roles = user.roles[:]
    formdef2.fields = [fields.StringField(id='2', label='1st field', varname='foo')]
    formdef2.store()
    formdef2.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': 'XXX'}
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp.form['resubmit'].value = formdef2.id
    resp = resp.form.submit('button_resubmit')
    resp = resp.follow()
    assert 'resubmitted' in resp.text
    assert formdef2.data_class().select()[0].status == 'draft'
    assert formdef2.data_class().select()[0].data == {'2': 'XXX'}
    resp = resp.click('resubmitted')
    resp = resp.follow()
    resp = resp.follow()
    assert resp.form['f2'].value == 'XXX'
    assert 'Original form' in resp.text
    assert formdata.get_url(backoffice=True) in resp.text


def test_backoffice_workflow_display_form(pub):
    user = create_user(pub)
    create_environment(pub)

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='str', required='required')
    )
    display_form.formdef.fields.append(
        # mimick special case: https://dev.entrouvert.org/issues/14691
        # item field displayed as radio buttons, with prefill of a value
        # that doesn't exist.
        fields.ItemField(
            id='2',
            label='Test2',
            prefill={'type': 'string', 'value': ''},
            display_mode='radio',
            varname='radio',
            items=['a', 'b', 'c'],
            required='required',
        )
    )

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = 'accepted'

    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.store()

    for formdata in formdef.data_class().select():
        if formdata.status == 'wf-new':
            break
    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    resp.form[f'fblah_{display_form.id}_1'] = 'blah'
    # don't fill required radio button
    resp = resp.form.submit('submit')
    assert formdef.data_class().get(formdata.id).status == 'wf-new'
    assert 'There were errors processing your form.' in resp.text
    resp.form[f'fblah_{display_form.id}_2'] = 'c'
    resp = resp.form.submit('submit')
    assert formdef.data_class().get(formdata.id).status == 'wf-accepted'
    assert formdef.data_class().get(formdata.id).workflow_data == {
        'blah_var_str': 'blah',
        'blah_var_radio': 'c',
        'blah_var_radio_raw': 'c',
        'blah_var_radio_structured': None,
        'blah_var_radio_structured_raw': None,
    }


def test_backoffice_workflow_form_with_conditions(pub):
    user = create_user(pub)
    create_environment(pub)

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(id='2', label='Test2', varname='str2', required='required'),
    ]

    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.fields[0].varname = 'plop'
    formdef.store()

    for formdata in formdef.data_class().select():
        if formdata.status == 'wf-new':
            break
    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    # check with static condition
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': '0'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub)).get(formdata.get_url(backoffice=True))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' not in resp.form.fields

    # check condition based on formdata
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': 'form_var_plop'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub)).get(formdata.get_url(backoffice=True))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': 'form_var_plop != "xxx"'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub)).get(formdata.get_url(backoffice=True))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    for variable_name in (
        'blah_var_str',
        'form_workflow_data_blah_var_str',
        'form_workflow_form_blah_var_str',
    ):
        # check with live conditions
        display_form.formdef.fields = [
            fields.StringField(id='1', label='Test', varname='str', required='required'),
            fields.StringField(
                id='2',
                label='Test2',
                varname='str2',
                required='required',
                condition={'type': 'django', 'value': '%s == "xxx"' % variable_name},
            ),
        ]
        wf.store()

        resp = login(get_app(pub)).get(formdata.get_url(backoffice=True))
        assert f'fblah_{display_form.id}_1' in resp.form.fields
        assert f'fblah_{display_form.id}_2' in resp.form.fields
        assert (
            resp.html.find('div', {'data-field-id': f'blah_{display_form.id}_1'}).attrs['data-live-source']
            == 'true'
        )
        assert (
            resp.html.find('div', {'data-field-id': f'blah_{display_form.id}_2'}).attrs.get('style')
            == 'display: none'
        )
        live_url = resp.html.find('form').attrs['data-live-url']
        assert '/backoffice/' in live_url
        resp.form[f'fblah_{display_form.id}_1'] = ''
        live_resp = app.post(live_url, params=resp.form.submit_fields())
        assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
        assert not live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

        resp.form[f'fblah_{display_form.id}_1'] = 'xxx'
        live_resp = app.post(live_url, params=resp.form.submit_fields())
        assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
        assert live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

        # check submit doesn't work
        resp = resp.form.submit('submit')
        assert 'There were errors processing your form.' in resp.text

        resp.form[f'fblah_{display_form.id}_1'] = 'xxx2'
        live_resp = app.post(live_url, params=resp.form.submit_fields())
        assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
        assert not live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

    # check submit does work when second field is hidden
    resp = resp.form.submit('submit').follow()

    assert formdef.data_class().get(formdata.id).workflow_data == {
        'blah_var_str': 'xxx2',
        'blah_var_str2': None,
    }


def test_backoffice_workflow_form_with_live_data_source(pub):
    user = create_user(pub)
    create_environment(pub)

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.ItemField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            data_source={'type': 'json', 'value': 'https://www.example.invalid/{{ blah_var_str }}'},
        ),
    ]

    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.fields[0].varname = 'plop'
    formdef.store()

    for formdata in formdef.data_class().select():
        if formdata.status == 'wf-new':
            break

    app = get_app(pub)

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://www.example.invalid/',
            json={'data': [{'id': 'A', 'text': 'hello'}, {'id': 'B', 'text': 'world'}]},
        )
        rsps.get(
            'https://www.example.invalid/toto',
            json={'data': [{'id': 'C', 'text': 'hello'}, {'id': 'D', 'text': 'world'}]},
        )

        resp = login(app).get(formdata.get_url(backoffice=True))
        assert f'fblah_{display_form.id}_1' in resp.form.fields
        assert f'fblah_{display_form.id}_2' in resp.form.fields
        assert resp.form.fields[f'fblah_{display_form.id}_2'][0].options == [
            ('A', False, 'hello'),
            ('B', False, 'world'),
        ]

        live_url = resp.html.find('form').attrs['data-live-url']
        resp.form[f'fblah_{display_form.id}_1'] = 'toto'
        live_resp = app.post(
            live_url,
            params=resp.form.submit_fields() + [('modified_field_id[]', f'blah_{display_form.id}_1')],
        )
        assert live_resp.json['result'][f'blah_{display_form.id}_2']['items'] == [
            {'text': 'hello', 'id': 'C'},
            {'text': 'world', 'id': 'D'},
        ]


def test_backoffice_workflow_display_form_with_block_add(pub):
    user = create_user(pub)
    create_environment(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.BlockField(id='2', label='Blocks', block_slug='foobar', varname='data', max_items='3'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = 'accepted'

    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.store()

    for formdata in formdef.data_class().select():
        if formdata.status == 'wf-new':
            break
    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp.form[f'fblah_{display_form.id}_1'] = 'blah'
    resp.form[f'fblah_{display_form.id}_2$element0$f123'] = 'foo'
    resp = resp.form.submit(f'fblah_{display_form.id}_2$add_element')
    resp.form[f'fblah_{display_form.id}_2$element1$f123'] = 'bar'
    resp = resp.form.submit('submit')

    assert formdef.data_class().get(formdata.id).workflow_data == {
        'blah_var_data': 'foobar, foobar',
        'blah_var_data_raw': {'data': [{'123': 'foo'}, {'123': 'bar'}], 'schema': {'123': 'string'}},
        'blah_var_str': 'blah',
    }


def test_backoffice_workflow_form_with_other_buttons(pub):
    user = create_user(pub)
    create_environment(pub)

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='str', required='required')
    )
    choice_ok = status.add_action('choice', id='_ok')
    choice_ok.label = 'OK'
    choice_ok.status = 'accepted'
    choice_ok.by = [user.roles[0]]

    choice_ko = status.add_action('choice', id='_ko')
    choice_ko.label = 'KO'
    choice_ko.status = 'accepted'
    choice_ko.ignore_form_errors = True
    choice_ko.by = [user.roles[0]]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = 'accepted'

    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.store()

    for button_name in ('submit', 'button_ok', 'button_ko'):
        formdata = formdef.data_class()()
        formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

        app = login(get_app(pub))
        resp = app.get(formdata.get_url(backoffice=True))
        resp.form[f'fblah_{display_form.id}_1'] = 'blah'
        resp = resp.form.submit(button_name)

        formdata.refresh_from_storage()
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        context = pub.substitutions.get_context_variables(mode='lazy')
        assert formdata.status == 'wf-accepted'
        if button_name == 'button_ko':
            assert context['form_workflow_data_blah_var_str'] == 'blah'  # leak
            with pytest.raises(KeyError):
                assert context['form_workflow_form_blah_var_str'] == 'blah'
        else:
            assert context['form_workflow_form_blah_var_str'] == 'blah'
            assert context['form_workflow_data_blah_var_str'] == 'blah'


def test_backoffice_criticality_formdata_view(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    wf.store()
    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow_id = wf.id
    formdef.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdata = [x for x in formdef.data_class().select() if x.status == 'wf-new'][0]

    formdata.set_criticality_level(1)
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'Criticality Level: yellow' in resp.text

    formdata.set_criticality_level(2)
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'Criticality Level: red' in resp.text


def test_workflow_jump_previous(pub):
    user = create_user(pub)
    create_environment(pub)

    wf = Workflow(name='jump around')
    #       North
    #     /      \
    # West <----> East
    #   |          |
    #   |         autojump
    #    |         |
    #     \       /
    #       South

    st1 = wf.add_status('North')
    st1.id = 'north'
    st2 = wf.add_status('West')
    st2.id = 'west'
    st3 = wf.add_status('East')
    st3.id = 'east'
    st4 = wf.add_status('Autojump')
    st4.id = 'autojump'
    st5 = wf.add_status('South')
    st5.id = 'south'

    button_by_id = {}

    def add_jump(label, src, dst_id):
        jump = src.add_action('choice', id=str(random.random()))
        jump.label = label
        jump.by = ['logged-users']
        jump.status = dst_id
        if dst_id != '_previous':
            jump.set_marker_on_status = True
        button_by_id[label] = 'button%s' % jump.id
        return jump

    add_jump('Go West', st1, st2.id)
    add_jump('Go East', st1, st3.id)
    add_jump('Go South', st2, st5.id)
    add_jump('Go Autojump', st3, st4.id)
    add_jump('Go Back', st5, '_previous')

    add_jump('Jump West', st3, st2.id)
    add_jump('Jump East', st2, st3.id)

    jump = st4.add_action('jump', id='_auto-jump')
    jump.status = st5.id

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)

    # jump around using buttons
    resp = resp.form.submit(button_by_id['Go West']).follow()  # push (north)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st2.id
    resp = resp.form.submit(button_by_id['Go South']).follow()  # push (north, west)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st5.id
    resp = resp.form.submit(button_by_id['Go Back']).follow()  # pop (north)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st2.id
    resp = resp.form.submit(button_by_id['Go South']).follow()  # push (north, west)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st5.id
    resp = resp.form.submit(button_by_id['Go Back']).follow()  # pop (north)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st2.id
    resp = resp.form.submit(button_by_id['Jump East']).follow()  # push (north, west)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st3.id
    resp = resp.form.submit(button_by_id['Go Autojump']).follow()  # push (north, west, east)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st5.id

    # check markers are displayed in /inspect page
    user.is_admin = True
    user.store()
    resp2 = app.get('/backoffice/management/form-title/%s/inspect' % formdata.id)
    assert 'Markers Stack' in resp2.text
    assert '<span class="status">East</span>' in resp2.text
    assert '<span class="status">West</span>' in resp2.text
    assert '<span class="status">North</span>' in resp2.text
    assert resp2.text.find('<span class="status">East</span>') < resp2.text.find(
        '<span class="status">West</span>'
    )
    assert resp2.text.find('<span class="status">West</span>') < resp2.text.find(
        '<span class="status">North</span>'
    )

    resp = resp.form.submit(button_by_id['Go Back']).follow()  # pop (north, west)
    assert formdef.data_class().get(formdata.id).status == 'wf-%s' % st3.id

    # and do a last jump using the API
    formdata = formdef.data_class().get(formdata.id)
    formdata.jump_status('_previous')  # pop (north)
    assert formdata.status == 'wf-%s' % st2.id

    formdata = formdef.data_class().get(formdata.id)
    formdata.jump_status('_previous')  # pop ()
    assert formdata.status == 'wf-%s' % st1.id


def test_workflow_jump_previous_on_submit(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('North')
    st1.id = 'north'
    st2 = wf.add_status('South')
    st2.id = 'south'

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.button_label = 'CLICK ME!'

    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id
    jump.set_marker_on_status = True

    back = st2.add_action('choice', id='_back')
    back.label = 'Back'
    back.by = ['_receiver']
    back.status = '_previous'

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp.form['comment'] = 'HELLO WORLD'
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()

    assert formdef.data_class().get(formdata.id).status == 'wf-south'
    assert formdef.data_class().get(formdata.id).workflow_data['_markers_stack']
    resp = resp.form.submit('button_back')
    resp = resp.follow()

    assert formdef.data_class().get(formdata.id).status == 'wf-north'
    assert not formdef.data_class().get(formdata.id).workflow_data['_markers_stack']


def test_workflow_jump_previous_auto(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('North')
    st1.id = 'north'
    st2 = wf.add_status('South')
    st2.id = 'south'

    jump = st1.add_action('jump', id='_auto-jump')
    jump.set_marker_on_status = True
    jump.status = st2.id

    back = st2.add_action('choice', id='_back')
    back.label = 'Back'
    back.by = ['_receiver']
    back.status = '_previous'

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-south'
    assert formdata.workflow_data['_markers_stack'] == [{'status_id': 'north'}]

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp = resp.form.submit('button_back')
    resp = resp.follow()

    # jumped and got back to north, then re-jump to south again (auto-jump)
    formdata = formdef.data_class().get(formdata.id)
    statuses = [evo.status for evo in formdata.evolution]
    assert statuses == ['wf-north', 'wf-south', 'wf-north', 'wf-south']
    # formdata went through north->south auto-jump again, status and marker are still here
    assert formdata.status == 'wf-south'
    assert formdata.workflow_data['_markers_stack'] == [{'status_id': 'north'}]

    # no marker (workflow inconsistency)
    formdata.workflow_data['_markers_stack'] = []
    formdata.store()
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp = resp.form.submit('button_back')
    resp = resp.follow()
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.status == 'wf-south'
    assert not formdata.workflow_data['_markers_stack']

    # unknown marker (workflow inconsistency)
    formdata.workflow_data['_markers_stack'] = [{'status_id': 'unknown_status'}]
    formdata.store()
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp = resp.form.submit('button_back')
    resp = resp.follow()
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.status == 'wf-south'


def test_backoffice_fields(pub):
    create_user(pub)

    wf = Workflow(name='bo fields')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(
            id='bo1', label='1st backoffice field', varname='backoffice_blah', required='optional'
        ),
    ]
    wf.add_status('Status1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'Backoffice Data' not in resp.text
    assert '1st backoffice field' not in resp.text

    formdata.data = {'bo1': 'HELLO WORLD'}
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'Backoffice Data' in resp.text
    assert '1st backoffice field' in resp.text
    assert 'HELLO WORLD' in resp.text

    wf.backoffice_fields_formdef.fields = [
        fields.StringField(
            id='bo1', label='1st backoffice field', varname='backoffice_blah', required='required'
        ),
    ]
    wf.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'Backoffice Data' in resp.text
    assert 'Not set' in resp.text


def test_backoffice_logged_errors(pub):
    Workflow.wipe()
    workflow = Workflow.get_default_workflow()
    workflow.id = '12'
    st1 = workflow.possible_status[0]
    jump = st1.add_action('jump', id='_jump', prepend=True)
    jump.status = 'rejected'
    jump.condition = {'type': 'django', 'value': '%'}  # TemplateSyntaxError
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.workflow = workflow
    formdef.name = 'test'
    formdef.confirmation = False
    formdef.fields = []
    formdef.store()

    # create a carddef with the same id
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = []
    carddef.store()

    assert formdef.id == carddef.id

    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert 'TemplateSyntaxError' not in resp.text
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert 'TemplateSyntaxError' not in resp.text
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'TemplateSyntaxError' not in resp.text

    app = get_app(pub)
    resp = app.get('/test/')
    resp = resp.form.submit('submit').follow()
    resp = resp.form.submit('submit')
    assert LoggedError.count([Null('deleted_timestamp')]) == 1

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert 'Failed to evaluate condition' in resp.text
    assert 'TemplateSyntaxError' in resp.text
    resp = resp.click('1 error')
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert 'TemplateSyntaxError' not in resp.text

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp2 = resp.click('1 error')
    assert 'Failed to evaluate condition' in resp2.text
    assert 'TemplateSyntaxError' in resp2.text
    resp = resp2.click('Failed to evaluate condition')
    assert 'TemplateSyntaxError: Could not parse the remainder' in resp.text
    assert 'Condition: <code>%</code>' in resp.text
    assert 'Condition type: <code>django</code>' in resp.text
    resp = resp.click('Delete').follow()
    assert LoggedError.count([Null('deleted_timestamp')]) == 0

    pub.cfg.update({'debug': {'error_email': None}})
    pub.write_cfg()

    app = get_app(pub)
    resp = app.get('/test/')
    resp = resp.form.submit('submit').follow()
    resp = resp.form.submit('submit')
    assert LoggedError.count([Null('deleted_timestamp')]) == 1

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'Failed to evaluate condition' in resp2.text
    assert 'TemplateSyntaxError' in resp2.text
    resp2 = resp.click('1 error')
    resp = resp2.click('Failed to evaluate condition')
    assert 'href="http://example.net/backoffice/management/test/' in resp.text

    # remove formdef
    FormDef.wipe()
    resp = resp.click('Failed to evaluate condition')
    assert 'href="http://example.net/backoffice/management/test/' not in resp.text


def test_backoffice_formdata_named_wscall(http_requests, pub):
    user = create_user(pub)

    FormDef.wipe()
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    formdef = FormDef()
    formdef.name = 'test'
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.fields = [
        fields.CommentField(id='7', label='X[webservice.hello_world.foo]Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))

    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'XbarY'

    # check with publisher variable in named webservice call
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'example_url', 'http://remote.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    wscall.request = {'url': '[example_url]json'}
    wscall.store()

    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'XbarY'

    # django-templated URL
    wscall.request = {'url': '{{ example_url }}json'}
    wscall.store()
    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'XbarY'

    # webservice call in django template
    formdef.fields = [
        fields.CommentField(id='7', label='dja-{{ webservice.hello_world.foo}}-ngo'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'dja-bar-ngo'

    # webservice call with django tag
    wscall.request['qs_data'] = {'test': '{{ parameters.xxx }}'}
    wscall.store()
    formdef.fields = [
        fields.CommentField(
            id='7', label='<p>{% webservice "hello_world" xxx="bar" as t %}dja-{{t.foo}}-ngo</p>'
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'dja-bar-ngo'
    assert http_requests.get_last('url') == 'http://remote.example.net/json?test=bar'

    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='{{ "bar" }}'),
        fields.CommentField(
            id='7', label='<p>{% webservice "hello_world" xxx=form_var_computed as t %}dja-{{t.foo}}-ngo</p>'
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/test/')
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'dja-bar-ngo'
    assert http_requests.get_last('url') == 'http://remote.example.net/json?test=bar'


def test_backoffice_session_var(pub):
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''[options]
query_string_allowed_vars = foo,bar
'''
        )

    FormDef.wipe()
    user = create_user(pub)

    formdef = FormDef()
    formdef.name = 'test'
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.fields = [
        fields.CommentField(id='7', label='X[session_var_foo]Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))

    resp = app.get('/backoffice/submission/test/?session_var_foo=bar')
    assert resp.location.endswith('/backoffice/submission/test/')
    resp = resp.follow()
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'XbarY'

    # django template
    formdef.fields = [
        fields.CommentField(id='7', label='d{{ session_var_foo }}o'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/test/?session_var_foo=jang')
    assert resp.location.endswith('/backoffice/submission/test/')
    resp = resp.follow()
    assert resp.html.find('div', {'data-field-id': '7'}).text.strip() == 'django'


def test_backoffice_display_message(pub):
    user = create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    display1 = st1.add_action('displaymsg')
    display1.message = 'message-to-all'
    display1.to = []

    display2 = st1.add_action('displaymsg')
    display2.message = 'message-to-submitter'
    display2.to = ['_submitter']

    display3 = st1.add_action('displaymsg')
    display3.message = 'message-to-receiver'
    display3.to = [user.roles[0]]

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.jump_status('st1')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))

    assert 'message-to-all' in resp.text
    assert 'message-to-submitter' not in resp.text
    assert 'message-to-receiver' in resp.text

    # display first message at the bottom of the page
    display1.position = 'bottom'
    workflow.store()
    resp = app.get(formdata.get_url(backoffice=True))
    assert resp.text.index('message-to-all') > resp.text.index('message-to-receiver')

    # display first message on top of actions
    display1.position = 'actions'
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=True))
    assert 'message-to-all' not in resp.text  # no actions no message

    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_receiver']
    again.status = st1.id
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=True))
    assert 'message-to-all' in resp.text
    assert resp.text.index('message-to-all') > resp.text.index('message-to-receiver')


def test_backoffice_forms_condition_on_button(pub):
    create_superuser(pub)
    create_environment(pub, set_receiver=True)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.workflow = workflow
    formdef.store()

    # move some forms from new to accepted
    for i, formdata in enumerate(formdef.data_class().select(lambda x: x.status == 'wf-new')):
        if i % 2:
            formdata.status = 'wf-accepted'
            formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert '17 open on 50' in resp.text

    formdata = [x for x in formdef.data_class().select() if x.status == 'wf-new'][0]
    resp = app.get(formdata.get_url(backoffice=True))
    assert 'button_commentable' in resp.text
    assert 'button_accept' in resp.text
    assert 'button_reject' in resp.text

    # commentable
    workflow.possible_status[1].items[0].condition = {'type': 'django', 'value': 'False'}
    # reject
    workflow.possible_status[1].items[2].condition = {'type': 'django', 'value': 'False'}
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=True))
    assert 'button_commentable' not in resp.text
    assert 'button_accept' in resp.text
    assert 'button_reject' not in resp.text

    formdef = FormDef.get_by_urlname('form-title')
    assert formdef.data_class().get(formdata.id).actions_roles == {'1'}

    # accept
    workflow.possible_status[1].items[1].condition = {'type': 'django', 'value': 'False'}
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=True))
    assert 'button_commentable' not in resp.text
    assert 'button_accept' not in resp.text
    assert 'button_reject' not in resp.text

    formdef = FormDef.get_by_urlname('form-title')
    assert formdef.data_class().get(formdata.id).actions_roles == set()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert '8 open on 50' in resp.text  # only the accepted ones


def test_workflow_comment_required(pub):
    create_user(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('Comment')
    st1.id = 'comment'
    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    assert 'widget-required' not in resp.text
    resp.form['comment'] = 'HELLO WORLD 1'
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()
    assert 'HELLO WORLD 1' in resp.text
    assert 'widget-required' not in resp.text
    resp.form['comment'] = None
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()

    commentable.required = True
    wf.store()
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    assert 'widget-required' in resp.text
    resp.form['comment'] = '  '  # spaces == empty
    resp = resp.form.submit('button_commentable')
    assert 'widget-with-error' in resp.text
    resp.form['comment'] = 'HELLO WORLD 2'
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()
    assert 'widget-with-error' not in resp.text
    assert 'HELLO WORLD 2' in resp.text


def test_lazy_eval_with_conditional_workflow_form(pub):
    Workflow.wipe()

    role = pub.role_class(name='foobar')
    role.store()
    user = create_user(pub)

    app = login(get_app(pub))

    FormDef.wipe()

    wf = Workflow(name='lazy backoffice form')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Foo Bar', varname='foo_bar'),
    ]
    st1 = wf.add_status('New', 'new')
    st2 = wf.add_status('Choose', 'choice')
    st3 = wf.add_status('Done', 'done')

    # first status with a workflow form, with a live conditional field.
    display_form = st1.add_action('form', id='_display_form')
    display_form.by = [user.roles[0]]
    display_form.varname = 'local'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test 2',
            varname='str2',
            condition={'type': 'django', 'value': 'local_var_str'},
        ),
    ]

    submit_choice = st1.add_action('jumponsubmit')
    submit_choice.status = st2.id

    # jump to a second status, that set's a backoffice field data
    setbo = st2.add_action('set-backoffice-fields')
    setbo.fields = [{'field_id': 'bo1', 'value': 'go'}]

    # and jump to the third status if the evoluation succeeds
    jump = st2.add_action('jump')
    jump.condition = {'type': 'django', 'value': "form_var_foo_bar == 'go'"}
    jump.status = st3.id
    wf.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='Foo', varname='foo')]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow_id = wf.id
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'0': 'test'}
    formdata.store()

    resp = app.get(formdata.get_url(backoffice=True))
    resp.forms[0][f'flocal_{display_form.id}_1'] = 'a'
    resp.forms[0][f'flocal_{display_form.id}_2'] = 'b'
    resp = resp.forms[0].submit()
    assert formdata.select()[0].status == 'wf-done'

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_var_foo_bar'] == 'go'


def test_backoffice_create_carddata_from_formdata(pub):
    CardDef.wipe()
    FormDef.wipe()

    user = create_user(pub, is_admin=True)
    user.name = 'Foo Bar'
    user.email = 'foo@example.com'
    user.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='string'),
        fields.ItemField(id='2', label='List', items=['item1', 'item2']),
        fields.DateField(id='3', label='Date'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    st1 = wf.add_status('New')
    st2 = wf.add_status('Create card')

    jump = st1.add_action('choice', id='_createcard')
    jump.label = 'Create card'
    jump.by = ['_receiver']
    jump.status = st2.id

    create_card = st2.add_action('create_carddata', id='_create')
    create_card.label = 'Create Card Data'
    create_card.varname = 'mycard'
    create_card.formdef_slug = carddef.url_name
    create_card.mappings = [
        Mapping(field_id='1', expression='Simple String'),
        Mapping(field_id='2', expression='{{ form_var_list_raw }}'),
        Mapping(field_id='3', expression='{{ form_var_date }}'),
    ]

    display_message = st2.add_action('displaymsg')
    display_message.message = 'Card nr. {{ form_links_mycard_form_number }} created'
    wf.store()

    formdef = FormDef()
    formdef.name = 'Source form'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.fields = [
        fields.ItemField(id='1', label='List', items=['item1', 'item2'], varname='list'),
        fields.DateField(id='2', label='Date', varname='date'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    today = time.strptime('2020-01-01', '%Y-%m-%d')
    formdata.data = {'1': 'item2', '2': today}

    formdata.user = user
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit(name='button_createcard').follow()
    assert 'Card nr. 1-1 created' in resp

    # visit inspect page
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect')
    assert '?expand=form_links_mycard' in resp.text
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect?expand=form_links_mycard')
    assert "variables from parent's request" in resp


def test_backoffice_after_submit_location(pub):
    create_superuser(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [logged_users_role().id]
    commentable.required = True

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-%s' % st1.id
    formdata.store()

    app = login(get_app(pub))

    resp = app.get(formdata.get_url(backoffice=True))
    resp.form['comment'] = 'plop'
    resp = resp.form.submit('submit')
    assert (
        resp.location == 'http://example.net/backoffice/management/form-title/%s/#action-zone' % formdata.id
    )
    resp = resp.follow()

    display = st1.add_action('displaymsg')
    display.message = 'message-to-all'
    display.to = []
    workflow.store()

    resp.form['comment'] = 'plop'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/#' % formdata.id


def test_backoffice_http_basic_auth(pub):
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    create_superuser(pub)
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    app.get('/backoffice/', status=403)


def test_backoffice_dispatch_lose_access(pub):
    user = create_user(pub)

    role1 = pub.role_class(name='xxx1')
    role1.store()
    role2 = pub.role_class(name='xxx2')
    role2.store()
    user.roles.append(role1.id)
    user.store()

    formdef = FormDef()
    formdef.name = 'test dispatch lose access'
    formdef.fields = []

    wf = Workflow(name='dispatch')

    st1 = wf.add_status('Status1')
    dispatch = st1.add_action('dispatch', id='_dispatch')
    dispatch.role_key = '_receiver'
    dispatch.role_id = role2.id

    add_function = st1.add_action('choice', id='_change_function')
    add_function.label = 'Change function'
    add_function.by = ['_receiver']
    add_function.status = st1.id

    wf.store()

    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': role1.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    resp = resp.form.submit('button_add_function')
    assert resp.location == '..'  # no access -> to listing


def test_backoffice_dispatch_multi(pub):
    user = create_user(pub)

    role1 = pub.role_class(name='xxx1')
    role1.store()
    role2 = pub.role_class(name='xxx2')
    role2.store()
    user.roles.append(role1.id)
    user.store()

    formdef = FormDef()
    formdef.name = 'test dispatch multi'
    formdef.fields = []

    wf = Workflow(name='dispatch')
    wf.roles['_foobar'] = 'Foobar'

    st1 = wf.add_status('Status1')
    dispatch = st1.add_action('dispatch', id='_dispatch')
    dispatch.role_key = '_receiver'
    dispatch.role_id = role2.id
    dispatch.operation_mode = 'add'

    add_function = st1.add_action('choice', id='_add_function')
    add_function.label = 'Add function'
    add_function.by = ['_receiver']
    add_function.status = st1.id

    wf.store()

    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': role1.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    resp = resp.form.submit('button_add_function').follow(status=200)  # access to role1 is still ok
    formdata.refresh_from_storage()
    assert formdata.workflow_roles == {'_receiver': [role1.id, role2.id]}


@pytest.mark.parametrize(
    'user_template',
    [
        '{{ session_user }}',
        '{{ session_user_email }}',
        '{{ session_user_email|upper }}',
        '{{ session_user_nameid }}',
        '{{ session_user_name }}',
        'foobar',  # a role, not an user
    ],
)
def test_backoffice_dispatch_single_user(pub, user_template):
    pub.user_class.wipe()
    user = create_user(pub)
    user.name_identifiers = ['0123456789']
    user.store()

    formdef = FormDef()
    formdef.name = 'test dispatch user'
    formdef.fields = []

    wf = Workflow(name='dispatch')
    wf.roles['_foobar'] = 'Foobar'

    st1 = wf.add_status('Status1')
    dispatch = st1.add_action('dispatch', id='_dispatch')
    dispatch.role_key = '_foobar'
    dispatch.role_id = user_template

    add_function = st1.add_action('choice', id='_add_function')
    add_function.label = 'Add function'
    add_function.by = ['_receiver']
    add_function.status = st1.id

    a_button = st1.add_action('choice', id='_a_button')
    a_button.label = 'A button'
    a_button.by = ['_foobar']
    a_button.status = st1.id

    wf.store()

    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata.id))
    assert 'button_a_button' not in resp.text
    resp = resp.form.submit('button_add_function').follow(status=200)
    formdata.refresh_from_storage()
    if user_template != 'foobar':
        assert formdata.workflow_roles == {'_foobar': ['_user:%s' % user.id]}
    else:
        # check role are still dispatched by name
        assert formdata.workflow_roles == {'_foobar': [user.roles[0]]}

    assert 'button_a_button' in resp.text


def test_backoffice_workflow_form_file_access(pub):
    FormDef.wipe()
    Workflow.wipe()

    role = pub.role_class(name='xxx1')
    role.store()

    user = create_superuser(pub)
    user.roles.append(role.id)
    user.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    next_status = wf.add_status('Next', 'st2')

    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_receiver']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.FileField(id='1', label='test', varname='file'),
        fields.StringField(id='2', label='test2', required='required'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = next_status.id

    wf.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp.form[f'fblah_{display_form.id}_1$file'] = Upload('test3.txt', b'foobar3', 'text/plain')
    resp = resp.form.submit('submit')
    # it will fail on the required string field; this allows testing
    # the temporary file URL.
    assert resp.click('test3.txt').body == b'foobar3'

    # check non-image files are returned as attachments
    resp = app.get(formdata.get_url(backoffice=True))
    resp.form[f'fblah_{display_form.id}_1$file'] = Upload('test3.html', b'foobar3', 'text/html')
    resp = resp.form.submit('submit')
    resp_file = resp.click('test3.html')
    assert resp_file.headers['Content-Disposition'] == 'attachment'


def test_backoffice_block_empty_value(pub):
    user = create_user(pub)
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test1', varname='t1'),
        fields.StringField(id='234', required='optional', label='Test2', varname='t2'),
    ]
    block.store()

    workflow = Workflow(name='test')
    workflow.roles = {'_receiver': 'Recipient'}
    workflow.add_status('new')

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.BlockField(
            id='bo1', label='Blocks', required='required', block_slug='foobar', max_items='1', varname='bo'
        ),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.fields = [
        fields.BlockField(id='1', label='Blocks', block_slug='foobar', max_items='3'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    app = login(get_app(pub))
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()
    formdata_id = resp.request.url.strip('/').split('/')[-1]
    # check the three values are displayed, but nothing for second field of second row
    assert resp.pyquery('.field-type-block .value .value').text() == 'foo bar baz'

    formdata = formdef.data_class().get(formdata_id)
    formdata.data['bo1'] = formdata.data['1']
    formdata.data['bo1_display'] = formdata.data['1_display']
    formdata.store()

    # check it behaves the same in backoffice, and for backoffice data
    resp = app.get(formdata.get_url(backoffice=True))
    # form data
    assert (
        resp.pyquery('.section.foldable.folded .dataview .field-type-block .value .value').text()
        == 'foo bar baz'
    )
    # backoffice data
    assert (
        resp.pyquery('.section.foldable:not(.folded) .dataview .field-type-block .value .value').text()
        == 'foo bar baz'
    )

    # check it displays "Not set", for backoffice data for unset required fields
    block.fields[1].required = 'required'
    block.store()
    resp = app.get(formdata.get_url(backoffice=True))
    assert (
        resp.pyquery('.section.foldable.folded .dataview .field-type-block .value .value').text()
        == 'foo bar baz'
    )
    assert (
        resp.pyquery('.section.foldable:not(.folded) .dataview .field-type-block .value .value').text()
        == 'foo bar baz Not set'
    )


def test_webservice_call_error_handling_with_marker(http_requests, pub):
    create_user(pub)
    create_environment(pub)
    app = login(get_app(pub))

    Workflow.wipe()
    wf = Workflow(name='error with marker')
    st1 = wf.add_status('One')
    st1.id = 'one'
    st2 = wf.add_status('Two')
    st2.id = 'two'
    ws_call = st1.add_action('webservice_call')
    ws_call.label = 'wscall'
    ws_call.url = 'http://remote.example.net/connection-error'
    ws_call.action_on_network_errors = st2.id
    ws_call.set_marker_on_status = True
    jump = st2.add_action('choice', id=str(random.random()))
    jump.label = 'back'
    jump.by = ['logged-users']
    jump.status = '_previous'
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    assert formdata.status == 'wf-two'
    resp = app.get('/backoffice/management/baz/%s/' % formdata.id)
    # jump back
    resp = resp.form.submit('button%s' % jump.id).follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-two'
    evolutions = formdata.evolution
    assert len(evolutions) == 4
    assert evolutions[0].status == 'wf-one'
    assert evolutions[1].status == 'wf-two'
    # jump back occured here
    assert evolutions[2].status == 'wf-one'
    # error handling of wscall
    assert evolutions[3].status == 'wf-two'


def test_anonymise_action_intermediate(pub):
    FormDef.wipe()
    Workflow.wipe()

    role = pub.role_class(name='xxx')
    role.store()
    user = create_user(pub, is_admin=True)
    user.name = 'Foo Bar'
    user.email = 'foo@example.com'
    user.roles.append(role.id)
    user.store()

    wf = Workflow(name='anonymise-action-intermediate')
    wf.id = '1'
    wf.roles = {'_receiver': role.id}
    anonymise_intermediate_status = wf.add_status('anonymise_intermediate', id='anonymise_intermediate')
    anonymise_intermediate_status.visibility = ['_receiver']
    anonymise_final_status = wf.add_status('anonymise_final', id='anonymise_final')
    anonymise_final_status.visibility = ['_receiver']

    anonymise_intermediate_action = anonymise_intermediate_status.add_action(
        'anonymise', id='_anonymise', prepend=True
    )
    anonymise_intermediate_action.label = 'Intermediate anonymisation'
    anonymise_intermediate_action.mode = 'intermediate'
    jump_to_anonymise_final = anonymise_intermediate_status.add_action('choice', id='_to_anonymise_final')
    jump_to_anonymise_final.by = ['_receiver']
    jump_to_anonymise_final.status = anonymise_final_status.id
    jump_to_anonymise_final.label = 'choice'
    jump_to_anonymise_final.identifier = 'id1'

    anonymise_final_action = anonymise_final_status.add_action('anonymise', id='_anonymise', prepend=True)
    anonymise_final_action.label = 'Final anonymisation'
    anonymise_final_action.mode = 'final'
    wf.store()

    formdef = FormDef()
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.name = 'Foo'
    formdef.workflow_id = wf.id
    formdef.fields = [
        StringField(id='0', label='string', anonymise='intermediate', varname='inter'),
        StringField(id='1', label='string', anonymise='final', varname='final'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Foo', '1': 'Bar'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-anonymise_intermediate'
    assert formdata.data == {'0': None, '1': 'Bar'}
    assert formdata.user_id

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/foo/%s/' % formdata.id)
    # check that anonymised data does not show up in the 'initial data' table
    assert len(resp.pyquery('fieldset.evolution--content-diff table tbody tr')) == 1
    assert resp.pyquery('fieldset.evolution--content-diff table tbody tr td')[2].text == 'Bar'

    resp = resp.form.submit('button_to_anonymise_final')
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-anonymise_final'
    assert formdata.data == {'0': None, '1': None}
    assert not formdata.user_id


def test_anonymise_action_final_also_deletes_fields_with_intermediate(pub):
    FormDef.wipe()
    Workflow.wipe()

    role = pub.role_class(name='xxx')
    role.store()
    user = create_user(pub, is_admin=True)
    user.name = 'Foo Bar'
    user.email = 'foo@example.com'
    user.roles.append(role.id)
    user.store()

    wf = Workflow(name='anonymise-action-final')
    wf.id = '1'
    wf.roles = {'_receiver': role.id}
    received_status = wf.add_status('received', id='anonymise_received')
    anonymise_final_status = wf.add_status('anonymise_final', id='anonymise_final')
    anonymise_final_status.visibility = ['_receiver']

    anonymise_final_action = anonymise_final_status.add_action('anonymise', id='_anonymise', prepend=True)
    anonymise_final_action.label = 'Final anonymisation'
    anonymise_final_action.mode = 'final'
    jump_to_anonymise_final = received_status.add_action('choice', id='_to_anonymise_final')
    jump_to_anonymise_final.by = ['_receiver']
    jump_to_anonymise_final.status = anonymise_final_status.id
    jump_to_anonymise_final.label = 'choice'
    jump_to_anonymise_final.identifier = 'id1'
    wf.store()

    formdef = FormDef()
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.name = 'Foo'
    formdef.workflow_id = wf.id
    formdef.fields = [
        StringField(id='0', label='string', anonymise='intermediate', varname='inter'),
        StringField(id='1', label='string', anonymise='final', varname='final'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Foo', '1': 'Bar'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-anonymise_received'
    assert formdata.data == {'0': 'Foo', '1': 'Bar'}

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/foo/%s/' % formdata.id)
    resp = resp.form.submit('button_to_anonymise_final')
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-anonymise_final'
    assert formdata.data == {'0': None, '1': None}


def test_status_visibility(pub):
    FormDef.wipe()
    Workflow.wipe()

    user = create_superuser(pub)

    wf = Workflow(name='visibility tests')
    wf.roles = {'_receiver': 'Receiver'}
    st1 = wf.add_status('st1')
    st2 = wf.add_status('st2')
    st3 = wf.add_status('st3')

    jump = st1.add_action('jump')
    jump.status = st2.id

    jump = st2.add_action('jump')
    jump.status = st3.id

    wf.store()

    formdef = FormDef()
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.name = 'Foo'
    formdef.workflow = wf
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub))
    assert [x.status for x in formdata.evolution] == ['wf-1', 'wf-2', 'wf-3']
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('.status').text() == 'st1 st2 st3'

    # set hidden status
    st2.set_visibility_mode('hidden')
    wf.store()
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('.status').text() == 'st1 st2 st3'

    # check with non-admin
    user.is_admin = False
    user.store()
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('.status').text() == 'st1 st3'

    # check status with an action for agent is made visible
    button = st2.add_action('choice')
    button.by = ['_receiver']
    button.label = 'plop'
    button.status = st3.id
    wf.store()
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('.status').text() == 'st1 st2 st3'


def test_backoffice_form_tracking_code_workflow_action(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()

    # as user
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> done
    formdata = formdef.data_class().select()[0]

    user = create_user(pub)
    resp = login(get_app(pub)).get('/backoffice/management/').follow()
    resp.forms[0]['query'] = formdata.tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    resp.forms['wf-actions']['comment'] = 'Test comment'
    resp = resp.forms['wf-actions'].submit('button_commentable')

    # check action has been recorded as agent
    formdata.refresh_from_storage()
    assert isinstance(formdata.evolution[-1].parts[0], WorkflowCommentPart)
    assert formdata.evolution[-1].who == str(user.id)


def test_backoffice_compact_table_view(pub):
    user = create_user(pub)

    workflow = Workflow(name='workflow')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]
    workflow.add_status('status1')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='1', label='string')]
    formdef.workflow = workflow
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'data', 'bo1': 'data'}
    formdata.just_created()
    formdata.store()

    app = get_app(pub)

    resp = login(app).get(formdata.get_backoffice_url())
    assert resp.pyquery('#compact-table-dataview-switch input')
    assert not resp.pyquery('#compact-table-dataview-switch input:checked')
    assert resp.pyquery('.dataview:not(.compact-dataview)')
    assert not resp.pyquery('.dataview.compact-dataview')

    app.post_json('/api/user/preferences', {'use-compact-table-dataview': True}, status=200)
    resp = app.get(formdata.get_backoffice_url())
    assert resp.pyquery('#compact-table-dataview-switch input')
    assert resp.pyquery('#compact-table-dataview-switch input:checked')
    assert not resp.pyquery('.dataview:not(.compact-dataview)')
    assert resp.pyquery('.dataview.compact-dataview')


def test_backoffice_listing_ajax(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 17

    # check status filter <select>
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'done'
    # simulate js
    resp.forms['listing-settings'].action = 'view-settings?ajax=on'
    resp2 = resp.forms['listing-settings'].submit()
    assert resp2.json['qs']
    assert resp2.json['uri']
    assert resp2.json['content'].count('data-link') == 20

    # add filter
    formdef = FormDef.get_by_urlname('form-title')
    resp.forms['listing-settings']['filter-%s' % formdef.fields[1].id].checked = True
    resp.forms['listing-settings'].action = 'view-settings?ajax=on'
    resp = resp.forms['listing-settings'].submit()
    resp = app.get(resp.json['uri'])  # full reload on new filters
    resp.forms['listing-settings']['filter-%s-value' % formdef.fields[1].id] = 'baz'
    resp.forms['listing-settings'].action = 'view-settings?ajax=on'
    resp = resp.forms['listing-settings'].submit()
    assert resp.json['content'].count('data-link') == 16
    uri_with_token = resp.json['uri']

    # save custom view
    resp = app.get(resp.json['uri'])
    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp = resp.forms['save-custom-view'].submit()
    custom_view = pub.custom_view_class.select()[0]
    # check it has relevant settings
    assert custom_view.filters == {
        'filter': 'done',
        f'filter-{formdef.fields[1].id}': 'on',
        f'filter-{formdef.fields[1].id}-operator': 'eq',
        f'filter-{formdef.fields[1].id}-value': 'baz',
        'filter-operator': 'eq',
        'filter-status': 'on',
    }

    # re-login, new session
    app = login(get_app(pub))
    resp = app.get(uri_with_token)
    assert resp.text.count('data-link') == 17  # back to initial result, obsolete token is ignored


def test_backoffice_listing_invalid_action_parameter(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['action'] = 'plop'
    resp.forms['listing-settings'].submit(status=400)


def test_workflow_track_jumps(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('One')
    st1.id = 'one'
    st2 = wf.add_status('Two')
    st2.id = 'two'

    st3 = wf.add_status('Three')
    st3.id = 'three'
    st4 = wf.add_status('Four')
    st4.id = 'four'
    st5 = wf.add_status('Five')
    st5.id = 'five'
    st6 = wf.add_status('Six')
    st6.id = 'six'

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.button_label = 'CLICK ME!'

    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id
    jump.identifier = 'to_two'

    to_three = st2.add_action('choice', id='_tothree')
    to_three.label = 'ToThree'
    to_three.by = ['_receiver']
    to_three.status = 'three'
    to_three.identifier = 'to_three'

    to_four = st3.add_action('choice', id='_tofour')
    to_four.label = 'ToFour'
    to_four.by = ['_receiver']
    to_four.status = 'four'
    to_four.identifier = 'to_four'

    to_five = st4.add_action('jump', id='_jump')
    to_five.status = 'five'
    to_five.identifier = 'to_five'

    to_six = st5.add_action('jump', id='_jump')
    to_six.status = 'six'
    to_six.identifier = 'to_six'
    to_six.mode = 'timeout'
    to_six.timeout = 30 * 60  # 30 minutes

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp.form['comment'] = 'HELLO WORLD'
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()

    formdata.refresh_from_storage()
    assert formdata.status == 'wf-two'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['to_two']
    assert substitution_variables['form_latest_jump'] == 'to_two'

    resp = resp.form.submit('button_tothree')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-three'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['to_two', 'to_three']
    assert substitution_variables['form_latest_jump'] == 'to_three'

    resp = resp.form.submit('button_tofour')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-five'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == [
        'to_two',
        'to_three',
        'to_four',
        'to_five',
    ]
    assert substitution_variables['form_latest_jump'] == 'to_five'

    formdata.receipt_time = formdata.receipt_time - datetime.timedelta(seconds=-to_six.timeout)
    formdata.evolution[-1].time = formdata.evolution[-1].time - datetime.timedelta(seconds=to_six.timeout)
    formdata.store()

    _apply_timeouts(pub)
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-six'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == [
        'to_two',
        'to_three',
        'to_four',
        'to_five',
        'to_six',
    ]
    assert substitution_variables['form_latest_jump'] == 'to_six'


def test_workflow_track_jumps_no_identifier(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('One')
    st1.id = 'one'
    st2 = wf.add_status('Two')
    st2.id = 'two'

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    commentable.button_label = 'CLICK ME!'

    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)
    resp.form['comment'] = 'HELLO WORLD'
    resp = resp.form.submit('button_commentable')
    resp = resp.follow()

    formdata.refresh_from_storage()
    assert formdata.status == 'wf-two'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == []
    assert substitution_variables['form_latest_jump'] == ''
    assert list(formdata.iter_evolution_parts(klass=JumpEvolutionPart)) == []


def test_workflow_track_jumps_does_not_store_useless_part(pub):
    create_user(pub)
    create_environment(pub)

    wf = Workflow(name='blah')
    st1 = wf.add_status('One')
    st1.id = 'one'
    st2 = wf.add_status('Two')
    st2.id = 'two'

    to_one = st1.add_action('choice', id='_toone')
    to_one.label = 'ToOne'
    to_one.by = ['_receiver']
    to_one.status = 'one'
    to_one.identifier = 'to_one'

    to_two = st1.add_action('choice', id='_totwo')
    to_two.label = 'ToTwo'
    to_two.by = ['_receiver']
    to_two.status = 'two'
    to_two.identifier = 'to_two'

    wf.store()

    formdef = FormDef.get_by_urlname('form-title')
    formdef.data_class().wipe()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/%s/' % formdata.id)

    resp = resp.form.submit('button_toone')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-one'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['to_one']
    assert substitution_variables['form_latest_jump'] == 'to_one'
    assert len(list(formdata.iter_evolution_parts(klass=JumpEvolutionPart))) == 1

    # do it again, check that no extra part is stored
    resp = resp.form.submit('button_toone')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-one'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['to_one']
    assert substitution_variables['form_latest_jump'] == 'to_one'
    assert len(list(formdata.iter_evolution_parts(klass=JumpEvolutionPart))) == 1

    # then move on to status two
    resp = resp.form.submit('button_totwo')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-two'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['to_one', 'to_two']
    assert substitution_variables['form_latest_jump'] == 'to_two'
    assert len(list(formdata.iter_evolution_parts(klass=JumpEvolutionPart))) == 2
