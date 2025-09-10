import json
import os
import re
import uuid
import xml.etree.ElementTree as ET

import pytest
import responses
from pyquery import PyQuery
from webtest import Upload

from wcs import fields, workflow_tests
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import WorkflowCategory
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.errors import ConnectionError
from wcs.qommon.http_request import HTTPRequest
from wcs.testdef import TestDef, TestResults
from wcs.wf.create_formdata import CreateFormdataWorkflowStatusItem, Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import (
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowVariablesFieldsFormDef,
    item_classes,
)

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


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


def test_workflows(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/workflows/')


def test_workflows_default(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    assert 'Default' in resp.text
    resp = resp.click(href=r'/backoffice/workflows/_default/$')
    assert 'Just Submitted' in resp.text
    assert 'This is the default workflow' in resp.text
    # makes sure it cannot be edited
    assert 'Edit' not in resp.text
    # and there's no history
    assert 'Save snapshot' not in resp.text
    assert 'History' not in resp.text

    # and makes sure status are not editable either
    resp = resp.click('Just Submitted')
    assert resp.pyquery('#appbar h2').text() == 'Just Submitted'
    assert 'Change Status Name' not in resp.text
    assert 'Delete' not in resp.text


def test_workflows_status_icons(pub):
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    # check status type icons/labels
    resp = app.get('/backoffice/workflows/1/')
    assert [
        (PyQuery(x).text(), re.search(r'status-type-[a-z]+', x.attrib['class']).group(0))
        for x in resp.pyquery('#status-list li')
    ] == [
        ('Just Submitted (transition status)', 'status-type-transition'),
        ('New (pause status)', 'status-type-waitpoint'),
        ('Rejected (final status)', 'status-type-endpoint'),
        ('Accepted (pause status)', 'status-type-waitpoint'),
        ('Finished (final status)', 'status-type-endpoint'),
    ]

    workflow.possible_status[1].loop_items_template = 'foobar'
    workflow.store()
    resp = app.get('/backoffice/workflows/1/')
    assert [
        (PyQuery(x).text(), re.search(r'status-type-[a-z]+', x.attrib['class']).group(0))
        for x in resp.pyquery('#status-list li')
    ] == [
        ('Just Submitted (transition status)', 'status-type-transition'),
        ('New (with loop) (pause status)', 'status-type-waitpoint'),
        ('Rejected (final status)', 'status-type-endpoint'),
        ('Accepted (pause status)', 'status-type-waitpoint'),
        ('Finished (final status)', 'status-type-endpoint'),
    ]

    workflow.possible_status[1].after_loop_status = str(workflow.possible_status[3].id)
    workflow.store()
    resp = app.get('/backoffice/workflows/1/')
    assert [
        (PyQuery(x).text(), re.search(r'status-type-[a-z]+', x.attrib['class']).group(0))
        for x in resp.pyquery('#status-list li')
    ] == [
        ('Just Submitted (transition status)', 'status-type-transition'),
        ('New (with loop) (transition status)', 'status-type-transition'),
        ('Rejected (final status)', 'status-type-endpoint'),
        ('Accepted (pause status)', 'status-type-waitpoint'),
        ('Finished (final status)', 'status-type-endpoint'),
    ]


def test_workflows_new(pub):
    create_superuser(pub)
    Workflow.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')

    # create a new workflow
    resp = resp.click('New Workflow')
    resp.forms[0]['name'] = 'a new workflow'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/'
    resp = resp.follow()
    assert 'There are not yet any status defined in this workflow' in resp.text
    assert '<svg ' not in resp.text

    # create a new status
    resp = resp.click('add status')
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click('add status')
    resp.forms[0]['name'] = 'new status'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/'
    resp = resp.follow()
    assert '<svg ' in resp.text
    assert '@import ' not in resp.text
    assert resp.click('Download').content_type == 'image/svg+xml'
    resp_fullscreen = resp.click('Full Screen')
    assert 'data-gadjo="true"' not in resp_fullscreen.text
    resp_fullscreen.click('Back to page')  # check link is ok

    # create a new action
    resp = resp.click('new status')
    resp.forms[0]['action-interaction'] = 'Alert'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert 'Use drag and drop' in resp.text
    assert resp.click('Download').content_type == 'image/svg+xml'
    resp_fullscreen = resp.click('Full Screen')
    assert 'data-gadjo="true"' not in resp_fullscreen.text
    resp_fullscreen.click('Back to page')  # check link is ok

    # fill action
    resp = resp.click('Alert')
    resp.forms[0]['message'] = 'bla bla bla'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/items/'
    resp = resp.follow()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'

    wf = Workflow.get(1)
    assert wf.name == 'a new workflow'
    assert wf.possible_status[0].name == 'new status'
    assert wf.possible_status[0].items[0].message == 'bla bla bla'


def test_workflows_status_same_name(pub):
    create_superuser(pub)
    Workflow.wipe()

    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(workflow.get_admin_url())
    resp = resp.click('add status')
    resp.forms[0]['name'] = 'baz'
    resp = resp.forms[0].submit()
    assert 'There is already a status with that name.' in resp.text

    resp.forms[0]['name'] = 'bar'
    resp = resp.forms[0].submit().follow()
    workflow = Workflow.get(workflow.id)
    assert len(workflow.possible_status) == 2


def test_workflows_svg(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [role.id]
    commentable.label = 'foobar'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/svg' % workflow.id)
    assert resp.content_type == 'image/svg+xml'
    assert '/static/css/dc2/admin.css' in resp.text

    resp = app.get('/backoffice/workflows/%s/status/%s/svg' % (workflow.id, st1.id))
    assert resp.content_type == 'image/svg+xml'
    assert '/static/css/dc2/admin.css' in resp.text

    assert '>baz<' in resp
    assert 'Jump after loop' not in resp
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/svg' % (workflow.id, st1.id))
    assert '>baz (With loop)<' in resp
    assert 'Jump after loop' in resp


def test_workflow_user_roles_svg(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    wf = Workflow(name='blah')
    st1 = wf.add_status('New')
    add_role = st1.add_action('add_role')
    remove_role = st1.add_action('remove_role')
    wf.store()

    resp = app.get('/backoffice/workflows/%s/svg' % wf.id)
    assert 'Role Addition (not' in resp
    assert 'Role Removal (not' in resp
    assert resp.text.count('configured)') == 2

    add_role.role_id = 'foobar'
    remove_role.role_id = 'barfoo'
    wf.store()

    resp = app.get('/backoffice/workflows/%s/svg' % wf.id)
    assert 'Role Addition' in resp
    assert '(unknown - foobar)' in resp
    assert 'Role Removal' in resp
    assert '(unknown - barfoo)' in resp

    role_a = pub.role_class(name='role A')
    role_a.store()
    role_b = pub.role_class(name='role B')
    role_b.store()
    add_role.role_id = role_a.id
    remove_role.role_id = role_b.id
    wf.store()

    resp = app.get('/backoffice/workflows/%s/svg' % wf.id)
    assert 'Role Addition (role' in resp
    assert 'A)' in resp
    assert 'Role Removal (role' in resp
    assert 'B)' in resp


def test_workflows_edit(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foo'
    assert resp.forms[0]['slug'].value == 'foo'
    assert 'data-slug-sync' in resp.text
    assert 'change-nevertheless' not in resp.text
    resp.forms[0]['name'] = 'baz'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/'
    resp = resp.follow()
    assert 'baz' in resp.text

    # title and slug are no longer in sync
    resp = resp.click(href='edit')
    assert 'data-slug-sync' not in resp.text
    assert 'change-nevertheless' not in resp.text


def test_workflows_edit_slug(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()
    workflow2 = Workflow(name='bar')
    workflow2.store()

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/workflows/{workflow.id}/edit')
    resp.forms[0]['slug'] = 'baz'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(workflow.id).slug == 'baz'

    resp = app.get(f'/backoffice/workflows/{workflow.id}/edit')
    resp.forms[0]['slug'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'This identifier is already used.' in resp.text


def test_workflows_category(pub):
    create_superuser(pub)

    WorkflowCategory.wipe()
    Workflow.wipe()

    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/')
    assert [x.attrib['href'] for x in resp.pyquery('.single-links a')] == [
        'http://example.net/backoffice/workflows/_default/',
        'http://example.net/backoffice/workflows/_carddef_default/',
        'http://example.net/backoffice/workflows/1/',
    ]
    assert 'Uncategorised' not in resp.text

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='category')
    assert 'There are not yet any category.' in resp.text

    resp = app.get('/backoffice/workflows/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a new category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert WorkflowCategory.get(1).name == 'a new category'

    # a category is defined -> an implicit "Uncategorised" section is displayed.
    resp = app.get('/backoffice/workflows/')
    assert [x.attrib['href'] for x in resp.pyquery('.single-links a')] == [
        'http://example.net/backoffice/workflows/_default/',
        'http://example.net/backoffice/workflows/_carddef_default/',
        'http://example.net/backoffice/workflows/1/',
    ]
    assert 'Uncategorised' in resp.text

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='category')
    resp.forms[0]['category_id'] = '1'
    resp = resp.forms[0].submit('cancel').follow()
    workflow.refresh_from_storage()
    assert workflow.category_id is None

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='category')
    resp.forms[0]['category_id'] = '1'
    resp = resp.forms[0].submit('submit').follow()
    workflow.refresh_from_storage()
    assert str(workflow.category_id) == '1'
    resp = app.get('/backoffice/workflows/')
    assert '<h2>a new category' in resp.text
    assert [x.attrib['href'] for x in resp.pyquery('.single-links a')] == [
        'http://example.net/backoffice/workflows/_default/',
        'http://example.net/backoffice/workflows/_carddef_default/',
        'http://example.net/backoffice/workflows/1/',
    ]
    assert 'Uncategorised' not in resp.text

    resp = app.get('/backoffice/workflows/categories/')
    resp = resp.click('New Category')
    resp.forms[0]['name'] = 'a second category'
    resp.forms[0]['description'] = 'description of the category'
    resp = resp.forms[0].submit('submit')
    assert WorkflowCategory.get(2).name == 'a second category'

    app.get('/backoffice/workflows/categories/update_order?order=2;1;')
    categories = WorkflowCategory.select()
    WorkflowCategory.sort_by_position(categories)
    assert [x.id for x in categories] == ['2', '1']

    app.get('/backoffice/workflows/categories/update_order?order=1;2;')
    categories = WorkflowCategory.select()
    WorkflowCategory.sort_by_position(categories)
    assert [x.id for x in categories] == ['1', '2']

    resp = app.get('/backoffice/workflows/categories/')
    resp = resp.click('a new category')
    resp = resp.click('Delete')
    resp = resp.forms[0].submit()
    workflow.refresh_from_storage()
    assert not workflow.category_id


def test_workflows_edit_status(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp = resp.click('Change Name')
    resp.forms[0]['name'] = 'bza'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].name == 'bza'

    resp = resp.click('Options')
    resp.forms[0]['visibility_mode'] = 'restricted'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].get_visibility_restricted_roles() == ['_receiver']

    resp = resp.click('Options')
    resp.forms[0]['visibility_mode'] = 'all'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert not Workflow.get(1).possible_status[0].get_visibility_restricted_roles()

    assert 'This status has been automatically evaluated as being terminal.' in resp.text
    resp = resp.click('Options')
    resp.forms[0]['force_terminal_status'].checked = True
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].forced_endpoint is True
    assert 'This status has been manually set to be considered as terminal.' in resp.text
    resp = resp.click('Options')
    resp.forms[0]['force_terminal_status'].checked = False
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()

    resp = resp.click('Options')
    assert resp.forms[0]['colour'].value == '#FFFFFF'
    assert resp.forms[0]['extra_css_class'].value == ''
    resp.forms[0]['colour'] = '#FF0000'
    resp.forms[0]['extra_css_class'] = 'plop'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].colour == '#FF0000'
    assert Workflow.get(1).possible_status[0].extra_css_class == 'plop'

    resp = resp.click('Options')
    assert resp.forms[0]['colour'].value == '#FF0000'
    assert resp.forms[0]['extra_css_class'].value == 'plop'
    resp.forms[0]['extra_css_class'] = 'xxx'
    resp = resp.forms[0].submit('cancel')
    assert Workflow.get(1).possible_status[0].extra_css_class == 'plop'
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()

    resp = resp.click('Options')
    assert resp.forms[0]['colour'].value == '#FF0000'
    assert resp.forms[0]['backoffice_info_text'].value == ''
    resp.forms[0]['backoffice_info_text'] = '<p>Hello</p>'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert 'Hello' in Workflow.get(1).possible_status[0].backoffice_info_text

    assert Workflow.get(1).possible_status[0].loop_items_template is None
    resp = resp.click('Options')
    resp.forms[0]['loop_items_template$value_template'].value = '{{ "abc"|make_list }}'
    resp.forms[0]['after_loop_status'].value = st2.id
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].loop_items_template == '{{ "abc"|make_list }}'
    assert Workflow.get(1).possible_status[0].after_loop_status == str(st2.id)
    assert (
        resp.pyquery('.workflow-status-loop-infos').text()
        == 'This status is configured to loop over actions. Once done, it will jump to this status: bar'
    )
    assert resp.click(href=resp.pyquery('.workflow-status-loop-infos a').attr.href).status_code == 200
    wf = Workflow.get(1)
    wf.possible_status[0].after_loop_status = '_previous'
    wf.store()
    resp = app.get(wf.possible_status[0].get_admin_url())
    assert (
        resp.pyquery('.workflow-status-loop-infos').text()
        == 'This status is configured to loop over actions. '
        'Once done, it will jump to the status that was previously marked.'
    )
    wf.possible_status[0].after_loop_status = 'invalid'
    wf.store()
    resp = app.get(wf.possible_status[0].get_admin_url())
    assert (
        resp.pyquery('.workflow-status-loop-infos').text()
        == 'This status is configured to loop over actions. '
        'It was configured to jump a to a specific status but it doesn\'t exist anymore.'
    )
    wf.possible_status[0].after_loop_status = ''
    wf.store()
    resp = app.get(wf.possible_status[0].get_admin_url())
    assert (
        resp.pyquery('.workflow-status-loop-infos').text()
        == 'This status is configured to loop over actions. '
        'It is not configured to jump to a specific status once done.'
    )

    resp = app.get(wf.possible_status[0].get_admin_url())
    resp = resp.click('Options')
    assert resp.forms[0]['loop_items_template$value_template'].value == '{{ "abc"|make_list }}'
    resp.forms[0]['loop_items_template$value_template'].value = ''
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(1).possible_status[0].loop_items_template is None
    assert 'This status is configured to loop over actions.' not in resp.text


def test_workflows_delete_status(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='bar')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('bar')

    resp = resp.click('Delete')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert len(Workflow.get(workflow.id).possible_status) == 2

    resp = resp.click('Delete')
    assert resp.pyquery('form .delete-button')
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/'
    resp = resp.follow()
    assert len(Workflow.get(workflow.id).possible_status) == 1

    resp = resp.click('baz')
    resp = resp.click('Delete')
    assert 'the workflow would not have any status left' in resp.text
    assert not resp.pyquery('form .delete-button')
    resp = resp.form.submit('submit')
    assert len(Workflow.get(workflow.id).possible_status) == 1


def test_workflows_unset_forced_endpoint(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st1.forced_endpoint = True
    workflow.store()

    resp = login(get_app(pub)).get(st1.get_admin_url())
    resp = resp.click('Unforce Terminal Status').follow()
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].forced_endpoint is False


@pytest.mark.parametrize('name', ['forms', 'cards', 'cards/forms'])
def test_workflows_delete_status_reassign(pub, name):
    formdef_classes = {
        'forms': (FormDef,),
        'cards': (CardDef,),
        'cards/forms': (FormDef, CardDef),
    }.get(name)
    create_superuser(pub)
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    wf_bar = workflow.add_status(name='bar')
    wf_baz = workflow.add_status(name='baz')
    workflow.store()

    formdefs = []
    for i, formdef_class in enumerate(formdef_classes):
        formdef = formdef_class()
        formdef.name = f'{formdef_class.xml_root_node} title {i}'
        formdef.workflow = workflow
        formdef.fields = []
        formdef.store()
        formdef.data_class().wipe()
        formdefs.append(formdef)

    app = login(get_app(pub))

    for action in ('nothing', 'remove', 'reassign'):
        formdefs[0].data_class().wipe()
        formdefs[-1].data_class().wipe()

        formdata1 = formdefs[0].data_class()()
        formdata1.status = 'wf-%s' % wf_bar.id
        formdata1.store()

        formdata2 = formdefs[-1].data_class()()
        formdata2.status = 'wf-%s' % wf_baz.id
        formdata2.store()

        if name == 'cards':
            # add a second one, to check for plural form in message.
            formdata2b = formdefs[-1].data_class()()
            formdata2b.status = 'wf-%s' % wf_baz.id
            formdata2b.store()

        workflow.store()
        AfterJob.wipe()

        resp = app.get('/backoffice/workflows/1/status/%s/' % wf_baz.id)
        resp = resp.click('Delete')
        resp = resp.form.submit('submit')
        assert resp.location.endswith('/reassign')
        resp = resp.follow()

        if action == 'nothing':
            resp.form['action'].value = ''
            resp = resp.form.submit('submit')
            resp = resp.follow()
            assert len(Workflow.get(workflow.id).possible_status) == 2
            assert formdef.data_class().get(formdata2.id).status == 'wf-%s' % wf_baz.id
            assert resp.request.path == f'/backoffice/workflows/{workflow.id}/status/{wf_baz.id}/'
            assert AfterJob.count() == 0
            continue

        if action == 'remove':
            resp.form['action'].value = 'remove'
            resp = resp.form.submit('submit')
            resp = resp.follow()
            assert formdefs[0].data_class().has_key(formdata1.id)
            assert not formdefs[-1].data_class().has_key(formdata2.id)
        elif action == 'reassign':
            resp.form['action'].value = f'reassign-{wf_bar.id}'
            resp = resp.form.submit('submit')
            resp = resp.follow()
            assert formdefs[-1].data_class().get(formdata2.id).status == 'wf-%s' % wf_bar.id

        if name in ('forms', 'cards'):
            assert AfterJob.count() == 3  # status change + rebuild_security + form or card tests
        else:
            assert AfterJob.count() == 4  # status change + rebuild_security + card tests + form tests
        resp = resp.click('Back')
        assert resp.request.path == f'/backoffice/workflows/{workflow.id}/'


def test_workflows_order_status(pub):
    Workflow.wipe()
    workflow = Workflow(name='Foobarbaz')
    workflow.add_status(name='foo')
    workflow.add_status(name='bar')
    workflow.add_status(name='baz')
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    status1_id = Workflow.get(workflow.id).possible_status[0].id
    status2_id = Workflow.get(workflow.id).possible_status[1].id
    status3_id = Workflow.get(workflow.id).possible_status[2].id
    app.get(
        '/backoffice/workflows/%s/update_order?order=%s;%s;%s;'
        % (workflow.id, status1_id, status2_id, status3_id)
    )
    assert Workflow.get(workflow.id).possible_status[0].id == status1_id
    assert Workflow.get(workflow.id).possible_status[1].id == status2_id
    assert Workflow.get(workflow.id).possible_status[2].id == status3_id
    app.get(
        '/backoffice/workflows/%s/update_order?order=%s;%s;%s;'
        % (workflow.id, status3_id, status2_id, status1_id)
    )
    assert Workflow.get(workflow.id).possible_status[0].id == status3_id
    assert Workflow.get(workflow.id).possible_status[1].id == status2_id
    assert Workflow.get(workflow.id).possible_status[2].id == status1_id

    # unknown id: ignored
    app.get(
        '/backoffice/workflows/%s/update_order?order=%s;%s;%s;0'
        % (workflow.id, status2_id, status3_id, status1_id)
    )
    assert Workflow.get(workflow.id).possible_status[0].id == status2_id
    assert Workflow.get(workflow.id).possible_status[1].id == status3_id
    assert Workflow.get(workflow.id).possible_status[2].id == status1_id

    # missing id: do nothing
    app.get('/backoffice/workflows/%s/update_order?order=%s;%s;' % (workflow.id, status3_id, status2_id))
    assert Workflow.get(workflow.id).possible_status[0].id == status2_id
    assert Workflow.get(workflow.id).possible_status[1].id == status3_id
    assert Workflow.get(workflow.id).possible_status[2].id == status1_id


def test_workflows_copy_status_item(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status('Status1')
    workflow.add_status('Status2')
    st3 = workflow.add_status('Status3')

    item = st1.add_action('sendmail')
    item.to = ['_submitter']
    item.subject = 'bla'
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    resp = resp.click('Copy')
    resp.form['status'] = 'Status3'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id)

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st3.id))
    resp = resp.click('Email')
    assert resp.form['to$element0$choice'].value == '_submitter'
    assert resp.form['subject'].value == 'bla'

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st3.id))
    resp = resp.click('Copy')
    assert resp.form['status'].value == 'Status3'
    resp.form['status'] = 'Status1'
    resp = resp.form.submit()
    assert resp.location == 'http://example.net/backoffice/workflows/%s/status/%s/' % (workflow.id, st3.id)

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    assert len(resp.pyquery('#items-list li')) == 2
    assert 'items/1/' in resp.text
    assert 'items/2/' in resp.text

    # check invalid role references are not copied
    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['unknown']
    workflow.store()

    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    resp = resp.click(href='items/_commentable/copy')
    resp = resp.form.submit('submit')
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[-1].by == []


def test_workflows_copy_status_item_create_document(pub):
    create_superuser(pub)
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status('Status1')
    st2 = workflow.add_status('Status2')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))

    resp.forms[0]['action-interaction'] = 'Document Creation'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Document Creation')
    resp.form['model_file'] = Upload('test.xml', b'<t>Model content</t>')
    resp = resp.form.submit('submit').follow().follow()
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    resp = resp.click('Copy')
    resp.form['status'] = 'Status2'
    resp = resp.form.submit()

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st2.id))
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'

    # modify file in initial status
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    resp = resp.click('Document Creation')
    resp.form['model_file'] = Upload('test2.xml', b'<t>Something else</t>')
    resp = resp.form.submit('submit').follow().follow()

    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test2.xml')
    assert resp_model_content.body == b'<t>Something else</t>'

    # check file is not changed in the copied item
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st2.id))
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'


def test_workflow_status_jump_sources(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status('status1')
    st2 = workflow.add_status('status2')
    st3 = workflow.add_status('status3')
    st4 = workflow.add_status('status4')
    st4.loop_items_template = 'plop'
    st4.after_loop_status = st2.id
    st3_jump = st3.add_action('jump')
    st3_jump.status = st1.id
    ac1 = workflow.add_global_action('action1')
    ac1_jump = ac1.add_action('jump')
    ac1_jump.status = st2.id
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/workflows/{workflow.id}/status/{st1.id}/')
    assert (
        resp.pyquery('.reach-statuses').text()
        == 'This status is reachable from the following status: status3'
    )
    resp.click('status3')  # check there's no 404

    resp = app.get(f'/backoffice/workflows/{workflow.id}/status/{st2.id}/')
    assert (
        resp.pyquery('.reach-statuses').text()
        == 'This status is reachable from the following status: status4'
    )
    assert (
        resp.pyquery('.reach-global-actions').text()
        == 'This status is reachable via the following global actions: action1'
    )
    resp.click('action1')  # check there's no 404


def test_workflows_delete(pub):
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'Form title'
    formdef.workflow = workflow
    formdef.fields = []
    formdef.store()

    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='delete')
    assert 'This workflow is currently in use, you cannot remove it.' in resp.text

    formdef.remove_self()

    carddef = CardDef()
    carddef.name = 'Card title'
    carddef.workflow = workflow
    carddef.fields = []
    carddef.store()

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='delete')
    assert 'This workflow is currently in use, you cannot remove it.' in resp.text

    carddef.remove_self()

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/'
    resp = resp.follow()
    assert Workflow.count() == 0


def test_workflows_usage(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'Usage' not in resp.text

    formdef = FormDef()
    formdef.name = 'Form title'
    formdef.workflow = workflow
    formdef.fields = []
    formdef.store()

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'Usage' in resp.text
    assert '/forms/%s/' % formdef.id in resp.text

    carddef = CardDef()
    carddef.name = 'Card title'
    carddef.workflow = workflow
    carddef.fields = []
    carddef.store()

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'Usage' in resp.text
    assert '/cards/%s/' % carddef.id in resp.text

    formdef.remove_self()

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert 'Usage' in resp.text
    assert '/cards/%s/' % carddef.id in resp.text

    # default workflow usage
    formdef = FormDef()
    formdef.name = 'Another form title'
    formdef.store()
    resp = app.get('/backoffice/workflows/_default/')
    assert 'Usage' in resp.text
    assert 'Another form title' in resp.text


def test_workflows_export_import(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='Foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Export')
    assert resp.content_type == 'application/x-wcs-workflow'
    wf_export = resp.body

    Workflow.wipe()

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/'
    resp = resp.follow()
    assert 'This workflow has been successfully imported' in resp.text
    assert Workflow.get(1).name == 'Foo'
    assert Workflow.get(1).slug == 'foo'

    # check second import
    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/2/'
    resp = resp.follow()
    assert 'This workflow has been successfully imported' in resp.text
    assert Workflow.get(2).name == 'Copy of Foo'
    assert Workflow.get(2).slug == 'copy-of-foo'

    Workflow.wipe()

    # different name, same slug
    workflow.id = None
    workflow.name = 'Foo2'
    workflow.store()  # store again
    assert workflow.slug == 'foo'

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit').follow()
    assert 'This workflow has been successfully imported' in resp.text
    assert Workflow.get(2).name == 'Foo'
    assert Workflow.get(2).slug == 'foo-1'

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', b'garbage')
    resp = resp.form.submit('submit')
    assert 'Invalid File' in resp.text
    assert Workflow.count() == 2


def test_workflows_import_from_url(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Export')
    assert resp.content_type == 'application/x-wcs-workflow'
    wf_export = resp.body

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp = resp.form.submit()
    assert 'You have to enter a file or a URL' in resp

    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.invalid/test.wcs', body=ConnectionError('...'))
        resp.form['url'] = 'http://remote.invalid/test.wcs'
        resp = resp.form.submit()
        assert 'Error loading form' in resp
        rsps.get('http://remote.invalid/test.wcs', body=wf_export.decode())
        resp.form['url'] = 'http://remote.invalid/test.wcs'
        resp = resp.form.submit()
    assert Workflow.count() == 2
    workflow = Workflow.get(2)
    assert workflow.import_source_url == 'http://remote.invalid/test.wcs'


def test_workflows_export_import_create_role(pub):
    create_superuser(pub)

    pub.role_class.wipe()
    role = pub.role_class()
    role.name = 'PLOP'
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [role.id]
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Export')
    assert resp.content_type == 'application/x-wcs-workflow'
    wf_export = resp.body

    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/2/'
    resp = resp.follow()
    assert 'This workflow has been successfully imported' in resp.text
    assert Workflow.get(2).name == 'Copy of foo'
    assert Workflow.get(2).possible_status[0].items[0].by == [role.id]

    role.remove_self()

    # automatically create role
    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/3/'
    resp = resp.follow()
    assert 'This workflow has been successfully imported' in resp.text
    assert Workflow.get(3).name == 'Copy of foo (2)'
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'PLOP'
    assert Workflow.get(3).possible_status[0].items[0].by == [pub.role_class.select()[0].id]

    # don't create role if they are managed by the identity provider
    pub.role_class.wipe()

    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()
    resp = app.get('/backoffice/workflows/')
    resp = resp.click('Import')
    resp.form['file'] = Upload('xxx.wcs', wf_export)
    resp = resp.form.submit('submit')
    assert 'Invalid File (Unknown referenced objects)' in resp
    assert '<ul><li>Unknown roles: PLOP</li></ul>' in resp


def test_workflows_duplicate(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    workflow.store()
    assert workflow.slug == 'foo'

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Duplicate')
    assert resp.form['name'].value == 'foo (copy)'
    resp = resp.form.submit('cancel').follow()
    assert Workflow.count() == 1
    resp = resp.click('Duplicate')
    assert resp.form['name'].value == 'foo (copy)'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/2/'
    resp = resp.follow()
    assert Workflow.get(2).name == 'foo (copy)'
    assert Workflow.get(2).slug == 'foo-copy'

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Duplicate')
    assert resp.form['name'].value == 'foo (copy 2)'
    resp.form['name'].value = 'other copy'
    resp = resp.form.submit('submit').follow()
    assert Workflow.get(3).name == 'other copy'
    assert Workflow.get(3).slug == 'other-copy'

    # check invalid role references are not copied
    commentable = st1.add_action('commentable', '_commentable')
    commentable.by = ['unknown']
    workflow.store()

    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Duplicate')
    resp = resp.form.submit('submit').follow()
    workflow_id = resp.request.url.split('/')[-2]
    duplicated_workflow = Workflow.get(workflow_id)
    assert duplicated_workflow.possible_status[0].items[0].by == []


def test_workflows_add_all_actions(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    for category in ('status-change', 'interaction', 'formdata-action', 'user-action'):
        for action in [x[0] for x in resp.forms[0]['action-%s' % category].options if x[0]]:
            resp.forms[0]['action-%s' % category] = action
            resp = resp.forms[0].submit()
            resp = resp.follow()

    i = 1
    for category in ('status-change', 'interaction', 'formdata-action', 'user-action'):
        for action in [x[0] for x in resp.forms[0]['action-%s' % category].options if x[0]]:
            resp = resp.click(href='items/%d/' % i, index=0)
            assert resp.pyquery('.pk-tabs--button-marker:not(#tab-general)').length == 0
            assert 'condition$value_django' in resp.forms[0].fields
            resp = resp.forms[0].submit('cancel')
            resp = resp.follow()  # redirect to items/
            resp = resp.follow()  # redirect to ./
            i += 1


def test_workflows_check_available_actions(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    assert 'Criticality Levels' not in [x[0] for x in resp.forms[0]['action-formdata-action'].options]
    assert 'SMS' not in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'User Notification' not in [x[0] for x in resp.forms[0]['action-interaction'].options]

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://www.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.cfg['sms'] = {'passerelle_url': 'xx', 'sender': 'xx'}
    pub.write_cfg()
    workflow.criticality_levels = [WorkflowCriticalityLevel(name='green')]
    workflow.store()
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')
    assert 'Criticality Levels' in [x[0] for x in resp.forms[0]['action-formdata-action'].options]
    assert 'SMS' in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'User Notification' in [x[0] for x in resp.forms[0]['action-interaction'].options]

    for action in ('Criticality Levels', 'SMS', 'User Notification'):
        for category in ('status-change', 'interaction', 'formdata-action', 'user-action'):
            if action in [x[0] for x in resp.forms[0]['action-%s' % category].options if x[0]]:
                resp.forms[0]['action-%s' % category] = action
                resp = resp.forms[0].submit()
                resp = resp.follow()

    for i in range(3):
        resp = resp.click(href='items/%d/' % (i + 1), index=0)
        resp = resp.forms[0].submit('cancel')
        resp = resp.follow()  # redirect to items/
        resp = resp.follow()  # redirect to ./


def test_workflows_status_reorder_actions(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    choice1 = st1.add_action('choice')
    choice1.by = ['logged-users']
    choice1.status = 'wf-%s' % st1.id
    choice1.label = 'choice1'
    choice2 = st1.add_action('choice')
    choice2.by = ['logged-users']
    choice2.status = 'wf-%s' % st1.id
    choice2.label = 'choice2'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    assert resp.text.index('choice1') < resp.text.index('choice2')

    resp = app.get(
        '/backoffice/workflows/%s/status/%s/update_order?order=%s;%s;'
        % (workflow.id, st1.id, choice1.id, choice2.id)
    )
    assert resp.json == {'err': 0}

    resp = app.get(
        '/backoffice/workflows/%s/status/%s/update_order?order=%s;%s;'
        % (workflow.id, st1.id, choice2.id, choice1.id)
    )
    assert resp.json == {'err': 0}

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st1.id))
    assert resp.text.index('choice2') < resp.text.index('choice1')

    resp = app.get(
        '/backoffice/workflows/%s/status/%s/update_order?order=%s;%s;' % (workflow.id, st1.id, '123', '456')
    )  # invalid action id
    assert resp.json == {'err': 1}


def test_workflows_edit_dispatch_action(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-formdata-action'] = 'Function/Role Linking'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Function/Role Linking')
    resp.form['rules$element0$value'].value = 'FOOBAR'
    resp.form['rules$element0$role_id'].value = str(role.id)
    resp = resp.form.submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    resp = resp.click('Function/Role Linking')
    assert resp.form['rules$element0$value'].value == 'FOOBAR'
    resp = resp.form.submit('rules$add_element')  # add one
    resp.form['rules$element1$value'].value = 'BARFOO'
    resp.form['rules$element1$role_id'].value = str(role.id)
    resp = resp.form.submit('submit')

    workflow = Workflow.get(workflow.id)
    assert workflow.possible_status[0].items[0].rules == [
        {'value': 'FOOBAR', 'role_id': '1'},
        {'value': 'BARFOO', 'role_id': '1'},
    ]


def test_workflows_edit_dispatch_action_repeated_function(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.roles = {
        '_receiver': 'Recipient',
        'manager': 'Manager',
        'manager2': 'Manager',
    }
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-formdata-action'] = 'Function/Role Linking'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Function/Role Linking')
    resp.form['role_key'].value = 'manager2'
    resp = resp.form.submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    workflow = Workflow.get(workflow.id)
    assert workflow.possible_status[0].items[0].role_key == 'manager2'


def test_workflows_edit_email_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Email'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Email')
    item_url = resp.request.url

    ok_strings = [
        'Hello world',  # ordinary string
        'Hello [world]',  # unknown reference
        'Hello [if-any world][world][end]',  # unknown reference, in condition
        'Hello world ][',  # random brackets
    ]

    for field in ('body', 'subject'):
        for ok_string in ok_strings:
            resp = app.get(item_url)
            if field == 'body':
                resp.form['subject'] = 'ok'
            else:
                resp.form['body'] = 'ok'
            resp.form[field] = ok_string
            resp = resp.form.submit('submit')
            assert resp.location

        resp = app.get(item_url)
        resp.form[field] = 'Hello {% if world %}{{ world }}{% else %}.'
        resp = resp.form.submit('submit')
        assert 'syntax error in Django template' in resp.text and 'Unclosed tag' in resp.text

        resp = app.get(item_url)
        resp.form[field] = 'Hello {% if world %}{{ world }}{% else %}.{% endif %}{% endif %}'
        resp = resp.form.submit('submit')
        assert 'syntax error in Django template' in resp.text and 'Invalid block tag' in resp.text

        resp = app.get(item_url)
        resp.form[field] = 'Hello [if-any world][world][else].'
        resp = resp.form.submit('submit')
        assert 'syntax error in ezt template' in resp.text and 'unclosed block' in resp.text

        resp = app.get(item_url)
        resp.form[field] = 'Hello [if-any world][world][else].[end] [end]'
        resp = resp.form.submit('submit')
        assert 'syntax error in ezt template' in resp.text and 'unmatched [end]' in resp.text

    # required fields
    resp = app.get(item_url)
    resp.form['subject'] = ''
    resp.form['body'] = ''
    resp = resp.form.submit('submit')
    assert [x.attrib['data-widget-name'] for x in resp.pyquery('.widget-with-error')] == ['subject', 'body']

    # attachments without backoffice fields: templates
    resp = app.get(item_url)
    assert 'Attachments (templates)' in resp.text
    resp.form['attachments$element0'] = '{{form_var_upload_raw}}'
    resp = resp.form.submit('submit')
    assert resp.location
    resp = app.get(item_url)
    assert 'Attachments (templates)' in resp.text
    assert resp.form['attachments$element0'].value == '{{form_var_upload_raw}}'
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == ['{{form_var_upload_raw}}']

    # attachments with backoffice fields: select-with-other inputs
    workflow = Workflow.get(workflow.id)
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1-1x', label='bo field 1', varname='upload'),
        fields.FileField(id='bo2-2x', label='bo field 2', varname='upload2'),
        fields.FileField(id='bo3-3x', label='bo field varnameless'),
    ]
    workflow.store()
    resp = app.get(item_url)
    assert 'Attachments' in resp.text
    assert 'Attachments (templates)' not in resp.text
    assert resp.form['attachments$element0$choice'].value == '{{form_var_upload_raw}}'
    assert len(resp.form['attachments$element0$choice'].options) == 5
    resp = resp.form.submit('attachments$add_element')  # add one
    resp.form['attachments$element1$choice'] = '{{form_var_upload2_raw}}'
    resp = resp.form.submit('submit')
    assert resp.location
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == ['{{form_var_upload_raw}}', '{{form_var_upload2_raw}}']

    resp = app.get(item_url)
    resp = resp.form.submit('attachments$add_element')  # add one
    resp.form['attachments$element2$choice'] = '{{form_fbo3_3x}}'
    resp = resp.form.submit('submit')
    assert resp.location
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == ['{{form_var_upload_raw}}', '{{form_var_upload2_raw}}', '{{form_fbo3_3x}}']

    resp = app.get(item_url)
    resp = resp.form.submit('attachments$add_element')  # add one
    resp.form['attachments$element3$choice'] = '__other'
    resp.form['attachments$element3$other'] = '{"content":"foo", "filename":"bar.txt"}'
    resp = resp.form.submit('submit')
    assert resp.location
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == [
        '{{form_var_upload_raw}}',
        '{{form_var_upload2_raw}}',
        '{{form_fbo3_3x}}',
        '{"content":"foo", "filename":"bar.txt"}',
    ]

    # remove some backoffice fields: varnameless fbo3 disapear
    workflow = Workflow.get(workflow.id)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo2', label='bo field 2', varname='upload2'),
    ]
    workflow.store()
    resp = app.get(item_url)
    resp = resp.form.submit('submit')
    assert resp.location
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == [
        '{{form_var_upload_raw}}',
        '{{form_var_upload2_raw}}',
        '{"content":"foo", "filename":"bar.txt"}',
    ]

    # remove all backoffice fields
    workflow = Workflow.get(workflow.id)
    workflow.backoffice_fields_formdef.fields = []
    workflow.store()
    resp = app.get(item_url)
    assert 'Attachments (templates)' in resp.text
    resp = resp.form.submit('submit')
    assert resp.location
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.attachments == [
        '{{form_var_upload_raw}}',
        '{{form_var_upload2_raw}}',
        '{"content":"foo", "filename":"bar.txt"}',
    ]

    # check condition has been saved as None, not {}.
    assert sendmail.condition is None

    resp = app.get(item_url)
    resp.form['condition$value_django'] = 'True'
    resp = resp.form.submit('submit')
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.condition == {'type': 'django', 'value': 'True'}

    # check "custom_from" is not advertised
    resp = app.get(item_url)
    assert 'custom_from' not in resp.text

    # check it's advertised if the appropriate site option is set
    pub.site_options.set('options', 'include-sendmail-custom-from-option', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(item_url)
    assert 'custom_from' in resp.text
    resp.form['custom_from$value_template'] = 'test@localhost'
    resp = resp.form.submit('submit')
    sendmail = Workflow.get(workflow.id).get_status(st1.id).items[0]
    assert sendmail.custom_from == 'test@localhost'

    # keep option displayed if it has a value
    pub.site_options.set('options', 'include-sendmail-custom-from-option', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(item_url)
    assert 'custom_from' in resp.text
    assert resp.form['custom_from$value_template'].value == 'test@localhost'


def test_workflows_edit_email_action_functions_only(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    action = st1.add_action('sendmail')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp = resp.click(href='items/1/', index=0)
    assert 'foobar' in [x[2] for x in resp.form['to$element0$choice'].options]
    assert '_receiver' in [x[0] for x in resp.form['to$element0$choice'].options]

    pub.site_options.set('options', 'workflow-functions-only', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(action.get_admin_url())
    assert 'foobar' not in [x[2] for x in resp.form['to$element0$choice'].options]
    assert '_receiver' in [x[0] for x in resp.form['to$element0$choice'].options]

    # behaviour when roles were directly set
    action.to = [str(role.id)]
    workflow.store()

    resp = app.get(action.get_admin_url())
    assert 'foobar' not in [x[2] for x in resp.form['to$element0$choice'].options]
    options = {x[0]: x[2] for x in resp.form['to$element0$choice'].options}
    assert options[role.id] == '❗ foobar (direct role, legacy)'


def test_workflows_edit_jump_previous(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')

    jump = st1.add_action('jump', id='_jump')
    jump.timeout = 86400
    jump.mode = 'timeout'

    ac1 = workflow.add_global_action('Action', 'ac1')

    jump_global = ac1.add_action('jump', id='_jump')
    jump_global.timeout = 86400

    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    assert 'Previously Marked Status' not in [x[2] for x in resp.form['status'].options]

    jump.set_marker_on_status = True
    workflow.store()
    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    assert 'Previously Marked Status' in [x[2] for x in resp.form['status'].options]

    jump.set_marker_on_status = False
    workflow.store()
    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    assert 'Previously Marked Status' not in [x[2] for x in resp.form['status'].options]

    jump_global.set_marker_on_status = True
    workflow.store()
    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    assert 'Previously Marked Status' in [x[2] for x in resp.form['status'].options]

    resp = app.get('/backoffice/workflows/1/global-actions/ac1/items/_jump/')
    assert 'Previously Marked Status' in [x[2] for x in resp.form['status'].options]
    assert 'trigger' not in resp.form.fields

    jump_global.set_marker_on_status = False
    workflow.store()
    resp = app.get('/backoffice/workflows/1/global-actions/ac1/items/_jump/')
    assert 'Previously Marked Status' not in [x[2] for x in resp.form['status'].options]


def test_workflows_edit_jump_timeout(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')

    jump = st1.add_action('jump', id='_jump')
    jump.status = '1'
    jump.timeout = 86400
    jump.mode = 'timeout'

    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    resp.form['timeout'] = ''
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].timeout is None
    assert 'Automatic Jump (to baz)' in resp.text

    resp = app.get('/backoffice/workflows/1/status/1/items/_jump/')
    resp.form['timeout'] = '90 minutes'
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].timeout == 5400
    assert 'Automatic Jump (to baz, timeout)' in resp.text


def test_workflows_edit_jump_trigger_functions_roles_label(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    jump = st1.add_action('jump')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(jump.get_admin_url())
    assert resp.pyquery('#form_label_by').text() == 'Functions or roles allowed to trigger'

    pub.site_options.set('options', 'workflow-functions-only', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(jump.get_admin_url())
    assert resp.pyquery('#form_label_by').text() == 'Functions allowed to trigger'


def test_workflows_jump_target_links(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')

    jump = st1.add_action('jump', id='_jump')
    jump.timeout = 86400
    jump.mode = 'timeout'
    jump.status = st2.id

    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/status/1/')
    assert resp.pyquery.find('.jump a').attr.href == 'http://example.net/backoffice/workflows/1/status/2/'

    for no_target in ('_previous', '_broken', None):
        jump.status = no_target
        workflow.store()
        resp = app.get('/backoffice/workflows/1/status/1/')
        assert not resp.pyquery.find('.jump a')

    # check jump url in history
    jump.status = st2.id
    workflow.store()
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('History')
    resp = resp.click('View', index=0)
    resp = resp.click('baz')
    snapshot_id = pub.snapshot_class.select_object_history(workflow)[0].id
    assert (
        resp.pyquery.find('.jump a').attr.href
        == f'http://example.net/backoffice/workflows/{workflow.id}/history/{snapshot_id}/view/status/{st2.id}/'
    )
    app.get(resp.pyquery.find('.jump a').attr.href, status=200)


def test_workflows_edit_jump_in_global_action(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    ac1 = workflow.add_global_action('Action', 'ac1')
    jump = ac1.add_action('jump', id='_jump')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(jump.get_admin_url())
    assert 'by' not in resp.form.fields
    assert 'timeout' not in resp.form.fields
    resp = resp.form.submit('submit')


def test_workflows_edit_sms_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    pub.cfg['sms'] = {'passerelle_url': 'xx', 'sender': 'xx'}
    pub.write_cfg()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.form['action-interaction'] = 'SMS'
    resp = resp.form.submit().follow()

    resp = resp.click('SMS')
    resp = resp.form.submit('to$add_element')
    resp = resp.form.submit('to$add_element')
    resp = resp.form.submit('to$add_element')
    resp.form['to$element1$value_template'] = '12345'
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).possible_status[0].items[0].to == ['12345']


def test_workflows_edit_attachment_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.form['action-interaction'] = 'Attachment'
    resp = resp.form.submit().follow()

    resp = resp.click('Attachment')
    assert not resp.form['document_type'].value
    resp.form['document_type'] = '_audio'
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].document_type == {
        'label': 'Sound files',
        'mimetypes': ['audio/*'],
        'id': '_audio',
    }

    resp = resp.click('Attachment')
    assert resp.form['document_type'].value == '_audio'
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].document_type == {
        'label': 'Sound files',
        'mimetypes': ['audio/*'],
        'id': '_audio',
    }

    # configure global filetypes
    pub.cfg['filetypes'] = {
        1: {'mimetypes': ['application/pdf', 'application/msword'], 'label': 'Text files'}
    }
    pub.write_cfg()

    resp = resp.click('Attachment')
    resp.form['document_type'] = '1'
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].document_type == {
        'label': 'Text files',
        'mimetypes': ['application/pdf', 'application/msword'],
        'id': 1,
    }

    # remove global filetype
    pub.cfg['filetypes'] = {}
    pub.write_cfg()

    # check its value is still selected
    resp = resp.click('Attachment')
    assert 'Text files' in [x[2] for x in resp.form['document_type'].options]
    assert resp.form['document_type'].value == '1'
    resp = resp.form.submit('submit').follow().follow()
    assert Workflow.get(workflow.id).possible_status[0].items[0].document_type == {
        'label': 'Text files',
        'mimetypes': ['application/pdf', 'application/msword'],
        'id': 1,
    }

    # check there's no portfolio option
    resp = resp.click('Attachment')
    assert 'allow_portfolio_picking' not in resp.form.fields
    assert 'push_to_portfolio' not in resp.form.fields
    resp = resp.form.submit('submit').follow().follow()

    pub.site_options.set('options', 'fargo_url', 'XXX')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = resp.click('Attachment')
    assert 'allow_portfolio_picking' in resp.form.fields
    assert 'push_to_portfolio' in resp.form.fields
    resp = resp.form.submit('submit').follow().follow()


def test_workflows_edit_display_form_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Form'
    resp = resp.forms[0].submit().follow()
    resp = resp.click(r'Form \(not completed\)')
    resp.form['varname'] = 'myform'
    resp = resp.forms[0].submit('submit').follow()
    # fields page
    resp.form['label'] = 'foobar'
    resp.form['type'] = 'string'
    resp = resp.form.submit()

    resp = resp.follow()
    assert 'foobar' in resp.text
    workflow.refresh_from_storage()
    resp = resp.click(href=r'^%s/$' % workflow.possible_status[0].items[0].formdef.fields[0].id)
    assert 'display_locations' not in resp.form.fields.keys()
    assert 'condition$value_django' in resp.form.fields.keys()
    resp = resp.form.submit('cancel')
    resp = resp.follow()
    resp = resp.click('Remove')
    assert 'You are about to remove the "foobar" field.' in resp.text
    assert 'Warning:' not in resp.text

    resp = app.get('/backoffice/workflows/1/status/1/items/1/')
    resp.form['varname'] = 'form'
    resp = resp.form.submit('submit')
    assert 'Wrong identifier detected: &quot;form&quot; prefix is forbidden.' in resp.text
    resp.form['varname'] = 'form_foo'
    resp = resp.form.submit('submit')
    assert 'Wrong identifier detected: &quot;form&quot; prefix is forbidden.' in resp.text
    resp.form['varname'] = 'formfoo'
    resp = resp.form.submit('submit')
    assert 'Wrong identifier detected: &quot;form&quot; prefix is forbidden.' not in resp.text


def test_workflows_edit_choice_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-status-change'] = 'Manual Jump'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click(href='items/1/', index=0)
    assert (
        resp.pyquery('select#form_status option')[1].attrib['data-goto-url']
        == 'http://example.net/backoffice/workflows/1/status/1/'
    )
    assert 'Previously Marked Status' not in [x[2] for x in resp.form['status'].options]
    resp.form['status'].value = baz_status.id
    resp = resp.form.submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    resp = resp.click(href='items/1/', index=0)
    resp.form['set_marker_on_status'].value = True
    resp = resp.form.submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    resp = resp.click(href='items/1/', index=0)
    assert 'data-goto-url' not in resp.pyquery('select#form_status option')[2].attrib
    assert 'Previously Marked Status' in [x[2] for x in resp.form['status'].options]


def test_workflows_edit_choice_action_functions_only(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    action = st1.add_action('choice')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp = resp.click(href='items/1/', index=0)
    assert 'foobar' in [x[2] for x in resp.form['by$element0'].options]
    assert '_receiver' in [x[0] for x in resp.form['by$element0'].options]

    pub.site_options.set('options', 'workflow-functions-only', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(action.get_admin_url())
    assert 'foobar' not in [x[2] for x in resp.form['by$element0'].options]
    assert '_receiver' in [x[0] for x in resp.form['by$element0'].options]

    # behaviour when roles were directly set
    action.by = [str(role.id)]
    workflow.store()

    resp = app.get(action.get_admin_url())
    assert 'foobar' not in [x[2] for x in resp.form['by$element0'].options]
    options = {x[0]: x[2] for x in resp.form['by$element0'].options}
    assert options[role.id] == '❗ foobar (direct role, legacy)'


def test_workflows_edit_choice_action_line_details(pub):
    create_superuser(pub)

    Workflow.wipe()
    wf = Workflow(name='foo')
    st1 = wf.add_status('New')
    st2 = wf.add_status('Resubmit')

    jump = st1.add_action('choice', id='1')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump (not completed)'

    jump.label = 'Resubmit'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump (not completed)'

    jump.status = st2.id
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("Resubmit", to Resubmit)'

    jump.label = 'Resubmit'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("Resubmit", to Resubmit)'

    jump.set_marker_on_status = True
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert (
        resp.html.find('a', {'href': 'items/1/'}).text
        == 'Manual Jump ("Resubmit", to Resubmit (and set marker))'
    )

    jump.set_marker_on_status = False
    jump.by = ['_submitter']
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("Resubmit", to Resubmit, by User)'

    jump.set_marker_on_status = True
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert (
        resp.html.find('a', {'href': 'items/1/'}).text
        == 'Manual Jump ("Resubmit", to Resubmit, by User (and set marker))'
    )

    jump.status = 'error'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert (
        resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump (broken, missing destination status)'
    )

    jump.status = '_previous'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert (
        resp.html.find('a', {'href': 'items/1/'}).text
        == 'Manual Jump ("Resubmit", to previously marked status, by User (and set marker))'
    )


def test_workflows_choice_action_line_details_markup(pub):
    create_superuser(pub)

    Workflow.wipe()
    wf = Workflow(name='foo')
    st1 = wf.add_status('New')

    jump = st1.add_action('choice', id='1')
    jump.status = '1'
    jump.parent = st1
    jump.label = '<b>test</b>'
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("<b>test</b>", to New)'
    # first svg <text> is status name, second is action label
    assert resp.pyquery('svg a text')[1].text == '<b>test</b>'

    jump.label = 'hello &world;'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("hello &world;", to New)'
    assert resp.pyquery('svg a text')[1].text == 'hello &world;'

    jump.label = 'hello &#129409;'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st1.id))
    assert resp.html.find('a', {'href': 'items/1/'}).text == 'Manual Jump ("hello &#129409;", to New)'
    assert resp.pyquery('svg a text')[1].text == 'hello &#129409;'


def test_workflows_action_subpath(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    baz_status.add_action('displaymsg', id='1')
    workflow.store()

    app = login(get_app(pub))
    app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))

    app.get('/backoffice/workflows/%s/status/%s/items/1/crash' % (workflow.id, baz_status.id), status=404)


def test_workflows_display_action_ezt_validation(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    baz_status.add_action('displaymsg')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['message'] = 'Hello world'
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).possible_status[0].items[0].message == 'Hello world'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['message'] = '{% if test %}test{% endif %}'  # valid Django
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).possible_status[0].items[0].message == '{% if test %}test{% endif %}'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['message'] = '{% if test %}test{% end %}'  # invalid Django
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template' in resp.text

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['message'] = '[if-any test]test[end]'  # valid ezt
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).possible_status[0].items[0].message == '[if-any test]test[end]'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['message'] = '[is test][end]'  # invalid ezt
    resp = resp.form.submit('submit')
    assert 'syntax error in ezt template' in resp.text


def test_workflows_delete_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Email'
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert 'Email' in resp.text

    resp = resp.click(href='items/1/delete')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    resp = resp.click(href='items/1/delete')
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/1/status/1/'
    resp = resp.follow()
    assert Workflow.get(workflow.id).possible_status[0].items == []


def test_workflows_variables(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='variables/')
    assert resp.location == 'http://example.net/backoffice/workflows/1/variables/fields/'
    resp = resp.follow()

    # makes sure we can't add page fields
    assert 'value="Page"' not in resp.text

    # add a simple field
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/variables/fields/'
    resp = resp.follow()

    # check it's been saved correctly
    assert 'foobar' in resp.text
    workflow = Workflow.get(1)
    assert len(workflow.variables_formdef.fields) == 1
    assert workflow.variables_formdef.fields[0].key == 'string'
    assert workflow.variables_formdef.fields[0].label == 'foobar'
    uuid.UUID(workflow.variables_formdef.fields[0].id)  # no ValueError


def test_workflows_variables_edit(pub):
    test_workflows_variables(pub)
    workflow = Workflow.get(1)

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='variables/', index=0)
    assert resp.location == 'http://example.net/backoffice/workflows/1/variables/fields/'
    resp = resp.follow()
    resp = resp.click(href=r'^%s/$' % workflow.variables_formdef.fields[0].id)
    assert resp.forms[0]['varname'].value == 'foobar'

    baz_status = workflow.add_status(name='baz')
    baz_status.add_action('displaymsg')
    workflow.store()

    resp = app.get('/backoffice/workflows/1/variables/fields/')
    resp = resp.click(href=r'^%s/$' % workflow.variables_formdef.fields[0].id)
    assert 'varname' in resp.forms[0].fields


def test_workflows_variables_default_value(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.StringField(id='123', label='hello', varname='hello'),
        fields.NumericField(id='234', label='world', varname='world'),
    ]
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/variables/fields/123/')
    assert not resp.pyquery('[data-widget-name="default_value"]')[0].attrib.get(
        'data-dynamic-display-child-of'
    )
    resp.forms[0]['default_value'].value = 'foo'
    resp = resp.forms[0].submit('submit')
    workflow = Workflow.get(1)
    assert workflow.variables_formdef.fields[0].default_value == 'foo'

    resp = app.get('/backoffice/workflows/1/variables/fields/123/')
    resp.forms[0]['default_value'].value = ''
    resp = resp.forms[0].submit('submit')
    workflow = Workflow.get(1)
    assert workflow.variables_formdef.fields[0].default_value is None

    resp = app.get('/backoffice/workflows/1/variables/fields/234/')
    assert not resp.pyquery('[data-widget-name="default_value"]')[0].attrib.get(
        'data-dynamic-display-child-of'
    )
    resp.forms[0]['default_value'].value = '999'
    resp = resp.forms[0].submit('submit')
    workflow = Workflow.get(1)
    assert workflow.variables_formdef.fields[1].default_value == 999

    resp = app.get('/backoffice/workflows/1/variables/fields/234/')
    resp.forms[0]['default_value'].value = ''
    resp = resp.forms[0].submit('submit')
    workflow = Workflow.get(1)
    assert workflow.variables_formdef.fields[1].default_value is None


def test_workflows_variables_edit_with_all_action_types(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    status = workflow.add_status(name='baz')
    for item_class in item_classes:
        status.add_action(item_class.key)
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='variables/')
    assert resp.location == 'http://example.net/backoffice/workflows/1/variables/fields/'
    resp = resp.follow()

    # add a simple field
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/variables/fields/'
    resp = resp.follow()


def test_workflows_variables_delete(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('http://example.net/backoffice/workflows/%s/variables/fields/' % workflow.id)

    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    assert len(Workflow.get(workflow.id).variables_formdef.fields) == 1
    resp = resp.click(href=resp.pyquery('#fields-list .remove a').attr.href)
    assert 'You are about to remove the "foobar" field.' in resp.text
    resp = resp.forms[0].submit()
    assert Workflow.get(workflow.id).variables_formdef is None


def test_workflows_variables_with_export_to_model_action(pub):
    test_workflows_variables(pub)

    workflow = Workflow.get(1)
    baz_status = workflow.add_status(name='baz')
    export_to = baz_status.add_action('export_to_model')
    export_to.label = 'create doc'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/variables/fields/')
    resp = resp.click(href=r'^%s/$' % workflow.variables_formdef.fields[0].id)


def test_workflows_backoffice_fields(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_id = workflow.id
    formdef.fields = []
    formdef.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'Test Block'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')
    assert 'Set Backoffice Field' not in resp.text

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='backoffice-fields/')
    assert resp.location == 'http://example.net/backoffice/workflows/1/backoffice-fields/fields/'
    resp = resp.follow()

    # makes sure we can't add page fields
    assert 'value="Page"' not in resp.text

    # add a simple field
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/backoffice-fields/fields/'
    resp = resp.follow()
    assert Workflow.get(workflow.id).get_backoffice_fields()[0].required == 'required'

    # check it's been saved correctly
    assert 'foobar' in resp.text
    assert len(Workflow.get(1).backoffice_fields_formdef.fields) == 1
    assert Workflow.get(1).backoffice_fields_formdef.fields[0].id.startswith('bo')
    assert Workflow.get(1).backoffice_fields_formdef.fields[0].key == 'string'
    assert Workflow.get(1).backoffice_fields_formdef.fields[0].label == 'foobar'

    assert 'New backoffice field "foobar"' in [
        x.comment for x in pub.snapshot_class.select_object_history(workflow)
    ]

    backoffice_field_id = Workflow.get(1).backoffice_fields_formdef.fields[0].id
    formdef = FormDef.get(formdef.id)
    data_class = formdef.data_class()
    data_class.wipe()
    formdata = data_class()
    formdata.data = {backoffice_field_id: 'HELLO'}
    formdata.status = 'wf-new'
    formdata.store()

    assert data_class.get(formdata.id).data[backoffice_field_id] == 'HELLO'

    # check the "set backoffice fields" action is now available
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')
    resp.forms[0]['action-formdata-action'] = 'Backoffice Data'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    # add a second field
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click(href='backoffice-fields/', index=0)
    assert resp.location == 'http://example.net/backoffice/workflows/1/backoffice-fields/fields/'
    resp = resp.follow()
    resp.forms[0]['label'] = 'foobar2'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/workflows/1/backoffice-fields/fields/'
    resp = resp.follow()
    workflow = Workflow.get(workflow.id)
    assert len(workflow.backoffice_fields_formdef.fields) == 2
    first_field_id = workflow.backoffice_fields_formdef.fields[0].id
    assert workflow.backoffice_fields_formdef.fields[1].id != first_field_id

    # check there's no prefill field
    resp = app.get(
        '/backoffice/workflows/1/backoffice-fields/fields/%s/'
        % workflow.backoffice_fields_formdef.fields[1].id
    )
    assert 'prefill$type' not in resp.form.fields.keys()

    # check display_locations
    resp.form['display_locations$element0'] = False
    resp.form['display_locations$element1'] = False
    resp = resp.form.submit('submit')
    assert (
        resp.location
        == 'http://example.net/backoffice/workflows/1/backoffice-fields/fields/#fieldId_%s'
        % workflow.backoffice_fields_formdef.fields[1].id
    )
    resp = resp.follow()
    workflow = Workflow.get(workflow.id)
    assert workflow.backoffice_fields_formdef.fields[1].display_locations is None

    # add a title field
    resp = app.get('/backoffice/workflows/1/backoffice-fields/fields/')
    resp.forms[0]['label'] = 'foobar3'
    resp.forms[0]['type'] = 'title'
    resp = resp.form.submit()
    workflow = Workflow.get(workflow.id)
    assert len(workflow.backoffice_fields_formdef.fields) == 3

    # add a block field
    resp = app.get('/backoffice/workflows/1/backoffice-fields/fields/')
    resp.forms[0]['label'] = 'foobar4'
    resp.forms[0]['type'] = 'block:test_block'
    resp = resp.form.submit()
    workflow = Workflow.get(workflow.id)
    assert len(workflow.backoffice_fields_formdef.fields) == 4

    # check backoffice fields are available in set backoffice fields action
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')  # status
    resp = resp.click('Backoffice Data')
    options = [x[2] for x in resp.form['fields$element0$field_id'].options]
    assert '' in options
    assert 'foobar - Text (line)' in options
    assert 'foobar2 - Text (line)' in options
    assert 'foobar3 - Title' not in options

    resp.form['fields$element0$field_id'] = first_field_id
    resp.form['fields$element0$value$value_template'] = 'Hello'
    resp = resp.form.submit('submit')
    workflow = Workflow.get(workflow.id)
    assert workflow.possible_status[0].items[0].fields == [{'field_id': first_field_id, 'value': 'Hello'}]

    # check backoffice fields have their type displayed on workflow page
    resp = app.get('/backoffice/workflows/1/')
    assert [PyQuery(x).text() for x in resp.pyquery('.backoffice-fields li')] == [
        'foobar Text (line) {{ form_\nvar_\nfoobar }}',
        'foobar2 Text (line) {{ form_\nvar_\nfoobar2 }}',
        'foobar3 Title',
        'foobar4 Block of fields (Test Block) {{ form_\nvar_\nfoobar4 }}',
    ]

    workflow.refresh_from_storage()
    workflow.backoffice_fields_formdef.fields[0].varname = 'foo_bar'
    workflow.store()
    resp = app.get('/backoffice/workflows/1/')
    assert '<code class="varname">{{ form_<wbr/>var_<wbr/>foo_<wbr/>bar }}</code>' in resp.text


def test_workflows_backoffice_fields_backlinks_to_actions(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', varname='bo1', label='bo1 variable'),
        fields.StringField(id='bo2', varname='bo2', label='bo2 variable'),
    ]
    status = workflow.add_status(name='baz')
    action1 = status.add_action('set-backoffice-fields')
    action1.fields = [{'field_id': 'bo1', 'value': '1'}]
    global_action = workflow.add_global_action('Update')
    action2 = global_action.add_action('set-backoffice-fields')
    action2.fields = [{'field_id': 'bo1', 'value': '2'}]
    action3 = global_action.add_action('set-backoffice-fields')
    action3.label = 'foobar'
    action3.fields = [{'field_id': 'bo1', 'value': '2'}]
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/workflows/{workflow.id}/backoffice-fields/fields/bo2/')
    assert not resp.pyquery('.actions-using-this-field')

    resp = app.get(f'/backoffice/workflows/{workflow.id}/backoffice-fields/fields/bo1/')
    assert {(x.text, x.attrib['href']) for x in resp.pyquery('.actions-using-this-field a')} == {
        ('Action in status "baz"', 'http://example.net/backoffice/workflows/1/status/1/items/1/'),
        (
            'Action in global action "Update"',
            'http://example.net/backoffice/workflows/1/global-actions/1/items/1/',
        ),
        (
            '"foobar" action in global action "Update"',
            'http://example.net/backoffice/workflows/1/global-actions/1/items/2/',
        ),
    }


def test_workflows_backoffice_fields_with_same_label(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', varname='bo1', label='variable'),
        fields.StringField(id='bo2', varname='bo2', label='variable'),
    ]
    status = workflow.add_status(name='baz')
    action1 = status.add_action('set-backoffice-fields')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(action1.get_admin_url())
    assert resp.form['fields$element0$field_id'].options == [
        ('', False, ''),
        ('bo1', False, 'variable - Text (line) (bo1)'),
        ('bo2', False, 'variable - Text (line) (bo2)'),
    ]


def test_workflows_fields_labels(pub):
    create_superuser(pub)

    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1-1x', label='hello' * 10),
        fields.CommentField(id='bo2-2x', label='<p>comment field</p>'),
    ]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.StringField(id='bo1-1x', label='hello' * 10, varname='hello'),
    ]
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(workflow.get_admin_url())
    assert [x.text.strip() for x in resp.pyquery('.backoffice-fields li a')] == [
        'hellohellohellohellohellohe(…)',
        'comment field',
    ]
    assert [x.text.strip() for x in resp.pyquery('.variables-fields li a')] == [
        'hellohellohellohellohellohe(…)',
    ]


def test_workflows_functions(pub):
    create_superuser(pub)

    CardDef.wipe()
    FormDef.wipe()

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add function')
    resp = resp.forms[0].submit('cancel')
    assert set(Workflow.get(workflow.id).roles.keys()) == {'_receiver'}

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add function')
    resp.forms[0]['name'] = 'Other Function'
    resp = resp.forms[0].submit('submit')
    assert set(Workflow.get(workflow.id).roles.keys()) == {'_receiver', '_other-function'}
    assert Workflow.get(workflow.id).roles['_other-function'] == 'Other Function'

    formdef = FormDef()
    formdef.name = 'Form title'
    formdef.workflow = workflow
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': None, '_other-function': None}
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Card title'
    carddef.workflow = workflow
    carddef.workflow_roles = {'_receiver': None, '_other-function': None}
    carddef.fields = []
    carddef.store()

    # test rename
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Other Function')
    resp = resp.forms[0].submit('cancel')

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Other Function')
    resp.forms[0]['name'] = 'Other Renamed Function'
    resp = resp.forms[0].submit('submit')
    assert set(Workflow.get(workflow.id).roles.keys()) == {'_receiver', '_other-function'}
    assert Workflow.get(workflow.id).roles['_other-function'] == 'Other Renamed Function'

    # test new function with older name
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add function')
    resp.forms[0]['name'] = 'Other Function'
    resp = resp.forms[0].submit('submit')
    assert set(Workflow.get(workflow.id).roles.keys()) == {
        '_receiver',
        '_other-function',
        '_other-function-2',
    }
    assert Workflow.get(workflow.id).roles['_other-function'] == 'Other Renamed Function'
    assert Workflow.get(workflow.id).roles['_other-function-2'] == 'Other Function'

    # test removal
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Other Renamed Function')
    resp = resp.forms[0].submit('delete')
    assert set(Workflow.get(workflow.id).roles.keys()) == {'_receiver', '_other-function-2'}

    formdef.refresh_from_storage()
    assert formdef.workflow_roles == {'_receiver': None}
    carddef.refresh_from_storage()
    assert carddef.workflow_roles == {'_receiver': None}

    # make sure it's not possible to remove the "_receiver" key
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Recipient')
    assert 'delete' not in resp.forms[0].fields


def test_workflows_functions_vs_visibility(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.store()

    # restrict visibility
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Just Submitted')
    resp = resp.click('Options')
    resp.forms[0]['visibility_mode'] = 'restricted'
    resp = resp.forms[0].submit()
    assert Workflow.get(workflow.id).possible_status[0].get_visibility_restricted_roles() == ['_receiver']

    # add function, make sure visibility follows
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add function')
    resp.forms[0]['name'] = 'Other Function'
    resp = resp.forms[0].submit('submit')
    assert set(Workflow.get(workflow.id).roles.keys()) == {'_receiver', '_other-function'}
    assert Workflow.get(workflow.id).roles['_other-function'] == 'Other Function'
    assert set(Workflow.get(workflow.id).possible_status[0].get_visibility_restricted_roles()) == {
        '_receiver',
        '_other-function',
    }

    # restrict visibility in a different status, check it gets all the
    # functions
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Rejected')
    resp = resp.click('Options')
    resp.forms[0]['visibility_mode'] = 'restricted'
    resp = resp.forms[0].submit()
    assert set(Workflow.get(workflow.id).possible_status[2].get_visibility_restricted_roles()) == {
        '_receiver',
        '_other-function',
    }


def test_workflows_global_actions(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp = resp.forms[0].submit('cancel')
    assert not Workflow.get(workflow.id).global_actions

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp.forms[0]['name'] = 'Global Action'
    resp = resp.forms[0].submit('submit')

    # test adding action with same name
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp.forms[0]['name'] = 'Global Action'
    resp = resp.forms[0].submit('submit')
    assert 'There is already an action with that name.' in resp.text

    # test rename
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Global Action')
    resp = resp.click('Change Name')
    resp = resp.form.submit('cancel')
    resp = resp.follow()
    resp = resp.click('Change Name')
    resp.forms[0]['name'] = 'Renamed Action'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(workflow.id).global_actions[0].name == 'Renamed Action'

    # test options
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Renamed Action')
    resp = resp.click('Options')
    resp = resp.form.submit('cancel')
    resp = resp.follow()
    resp = resp.click('Options')
    resp.forms[0]['backoffice_info_text'] = 'info text'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(workflow.id).global_actions[0].backoffice_info_text == 'info text'

    # test removal
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Renamed Action')
    resp = resp.click('Delete')
    resp = resp.form.submit('cancel')
    resp = resp.follow()
    resp = resp.click('Delete')
    resp = resp.form.submit('delete')
    assert not Workflow.get(workflow.id).global_actions


def test_workflows_global_actions_edit(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status('Status1')
    workflow.add_status('Status2')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp.forms[0]['name'] = 'Global Action'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    # test adding all actions
    for category in ('status-change', 'interaction', 'formdata-action', 'user-action'):
        for action in [x[0] for x in resp.forms[0]['action-%s' % category].options if x[0]]:
            resp.forms[0]['action-%s' % category] = action
            resp = resp.forms[0].submit()
            resp = resp.follow()

    # test visiting
    action_id = Workflow.get(workflow.id).global_actions[0].id
    for item in Workflow.get(workflow.id).global_actions[0].items:
        resp = app.get(
            '/backoffice/workflows/%s/global-actions/%s/items/%s/' % (workflow.id, action_id, item.id)
        )

    # test modifying a trigger
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Global Action')
    assert resp.pyquery('#triggers-list li > a').text() == 'Manual, not assigned'
    assert len(Workflow.get(workflow.id).global_actions[0].triggers) == 1
    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    assert resp.form['roles$element0'].value == 'None'
    resp.form['roles$element0'].value = '_receiver'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('#triggers-list li > a').text() == 'Manual, by Recipient'
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].roles == ['_receiver']
    # limit to some statuses
    resp = resp.click(href=resp.pyquery('#triggers-list li > a').attr.href, index=0)
    resp.form['statuses$element0'].value = st1.id
    resp = resp.form.submit('submit').follow()
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].statuses == [st1.id]
    assert resp.pyquery('#triggers-list li > a').text() == 'Manual, from status "Status1", by Recipient'

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Global Action')
    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    assert resp.form['roles$element0'].value == '_receiver'
    resp = resp.form.submit('roles$add_element')
    resp.form['roles$element1'].value = '_submitter'
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].roles == ['_receiver', '_submitter']

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Global Action')
    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    resp.form['roles$element1'].value = 'None'
    resp = resp.form.submit('submit')
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].roles == ['_receiver']


def test_workflows_global_order_actions_update(pub):
    Workflow.wipe()
    workflow = Workflow(name='Foobarbaz')
    workflow.add_global_action('Global 1')
    workflow.add_global_action('Global 2')
    workflow.add_global_action('Global 3')
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    action1_id = Workflow.get(workflow.id).global_actions[0].id
    action2_id = Workflow.get(workflow.id).global_actions[1].id
    action3_id = Workflow.get(workflow.id).global_actions[2].id
    app.get(
        '/backoffice/workflows/%s/update_actions_order?order=%s;%s;%s;'
        % (workflow.id, action1_id, action2_id, action3_id)
    )
    assert Workflow.get(workflow.id).global_actions[0].id == action1_id
    assert Workflow.get(workflow.id).global_actions[1].id == action2_id
    assert Workflow.get(workflow.id).global_actions[2].id == action3_id
    app.get(
        '/backoffice/workflows/%s/update_actions_order?order=%s;%s;%s;'
        % (workflow.id, action3_id, action2_id, action1_id)
    )
    assert Workflow.get(workflow.id).global_actions[0].id == action3_id
    assert Workflow.get(workflow.id).global_actions[1].id == action2_id
    assert Workflow.get(workflow.id).global_actions[2].id == action1_id

    # unknown id: ignored
    app.get(
        '/backoffice/workflows/%s/update_actions_order?order=%s;%s;%s;0'
        % (workflow.id, action2_id, action3_id, action1_id)
    )
    assert Workflow.get(workflow.id).global_actions[0].id == action2_id
    assert Workflow.get(workflow.id).global_actions[1].id == action3_id
    assert Workflow.get(workflow.id).global_actions[2].id == action1_id

    # missing id: do nothing
    app.get(
        '/backoffice/workflows/%s/update_actions_order?order=%s;%s;' % (workflow.id, action3_id, action2_id)
    )
    assert Workflow.get(workflow.id).global_actions[0].id == action2_id
    assert Workflow.get(workflow.id).global_actions[1].id == action3_id
    assert Workflow.get(workflow.id).global_actions[2].id == action1_id


def test_workflows_global_actions_timeout_triggers(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp.forms[0]['name'] = 'Global Action'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    # test removing the existing manual trigger
    resp = resp.click(href='triggers/%s/delete' % Workflow.get(workflow.id).global_actions[0].triggers[0].id)
    resp = resp.forms[0].submit()
    resp = resp.follow()

    assert len(Workflow.get(workflow.id).global_actions[0].triggers) == 0

    # test adding a timeout trigger
    resp.forms[1]['type'] = 'Automatic'
    resp = resp.forms[1].submit()
    resp = resp.follow()

    assert 'Automatic (not configured)' in resp.text

    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    for invalid_value in ('foobar', '-', '0123'):
        resp.form['timeout'] = invalid_value
        resp = resp.form.submit('submit')
        assert 'wrong format' in resp.text
    for invalid_value in ('833333335', '-833333335'):
        resp.form['timeout'] = invalid_value
        resp = resp.form.submit('submit')
        assert 'invalid value, out of bounds' in resp.text
    resp.form['timeout'] = ''
    resp = resp.form.submit('submit')
    assert 'required field' in resp.text
    resp.form['timeout'] = '3'
    resp = resp.form.submit('submit').follow()

    assert Workflow.get(workflow.id).global_actions[0].triggers[0].timeout == '3'

    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    resp.form['timeout'] = '-2'
    resp = resp.form.submit('submit').follow()
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].timeout == '-2'

    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    resp.form['timeout'] = '0'
    resp = resp.form.submit('submit').follow()
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].timeout == '0'

    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[0].id, index=0
    )
    resp.form['timeout'] = '{{ xxx }}'
    resp = resp.form.submit('submit').follow()
    assert Workflow.get(workflow.id).global_actions[0].triggers[0].timeout == '{{ xxx }}'


def test_workflows_global_actions_webservice_trigger(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add global action')
    resp.forms[0]['name'] = 'Global Action'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    # test adding a timeout trigger
    resp.forms[1]['type'] = 'External call'
    resp = resp.forms[1].submit()
    resp = resp.follow()

    assert 'External call (not configured)' in resp

    resp = resp.click(
        href='triggers/%s/' % Workflow.get(workflow.id).global_actions[0].triggers[-1].id, index=0
    )
    resp.form['identifier'] = 'foo bar'
    resp = resp.form.submit('submit')
    assert (
        resp.pyquery('.widget-with-error .error').text()
        == 'must only consist of letters, numbers, or underscore'
    )
    resp.form['identifier'] = 'foobar'
    resp = resp.form.submit('submit').follow()
    assert 'External call (foobar)' in resp


def test_workflows_global_actions_timeout_trigger_anchor_options(pub):
    create_superuser(pub)

    workflow = Workflow(name='global')
    action = workflow.add_global_action('Global')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/global-actions/1/triggers/%s/' % (workflow.id, trigger.id))
    assert resp.form['anchor'].options == [
        ('Creation', False, 'Creation'),
        ('First arrival in status', False, 'First arrival in status'),
        ('Latest arrival in status', False, 'Latest arrival in status'),
        ('Arrival in final status', False, 'Arrival in final status'),
        ('Anonymisation', False, 'Anonymisation'),
        ('String / Template', False, 'String / Template'),
    ]


def test_workflows_global_actions_external_workflow_action(pub):
    FormDef.wipe()
    CardDef.wipe()

    create_superuser(pub)
    Workflow.wipe()

    wf = Workflow(name='external')
    action = wf.add_global_action('Global action')
    trigger = action.append_trigger('webservice')
    action.add_action('remove')
    wf.store()

    formdef = FormDef()
    formdef.name = 'external'
    formdef.workflow = wf
    formdef.store()
    workflow = Workflow(name='foo')
    st = workflow.add_status('New')
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st.id))
    resp.forms[0]['action-formdata-action'] = 'External workflow'
    resp = resp.forms[0].submit().follow()
    assert 'External workflow (not completed)' in resp.text

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st.id))
    assert 'No workflow with external triggerable global action.' in resp.text

    trigger.identifier = 'test'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st.id))

    resp = resp.forms[0].submit('submit')
    assert 'required field' in resp.text
    resp.forms[0]['slug'] = 'formdef:%s' % formdef.url_name
    assert (
        resp.pyquery('select#form_slug option')[1].attrib['data-goto-url']
        == 'http://example.net/backoffice/forms/1/'
    )
    resp = resp.forms[0].submit('submit')
    assert 'required field' in resp.text
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['trigger_id'] = 'action:%s' % trigger.identifier
    resp = resp.forms[0].submit('submit').follow().follow()
    assert 'External workflow (action &quot;Global action&quot; on external)' in resp.text
    assert Workflow.get(workflow.id).possible_status[0].items[0].target_mode == 'all'
    assert Workflow.get(workflow.id).possible_status[0].items[0].target_id is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, st.id))
    resp.forms[0]['target_mode'] = 'manual'
    resp.forms[0]['target_id$value_template'] = '{{ form_var_plop_id }}'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(workflow.id).possible_status[0].items[0].target_mode == 'manual'
    assert Workflow.get(workflow.id).possible_status[0].items[0].target_id == '{{ form_var_plop_id }}'

    trigger.identifier = 'another_test'
    wf.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st.id))
    assert 'External workflow (not completed)' in resp.text

    trigger.identifier = 'action:%s' % trigger.identifier
    wf.store()
    formdef.remove_self()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, st.id))
    assert 'External workflow (not completed)' in resp.text
    resp = resp.click(href=re.compile(r'^items/1/$'), index=0)


def test_workflows_external_workflow_action_config(pub):
    create_superuser(pub)

    Workflow.wipe()
    external_wf = Workflow(name='external')
    action = external_wf.add_global_action('Global action')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'test'
    action.add_action('remove')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'test2'
    external_wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'external'
    formdef.workflow = external_wf
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'other'
    formdef2.workflow = external_wf
    formdef2.store()

    wf = Workflow(name='foo')
    st = wf.add_status('New')
    st.add_action('external_workflow_global_action')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['slug'] = 'formdef:external'
    assert [
        (x.attrib.get('value'), x.attrib.get('data-slugs'))
        for x in resp.pyquery('#form_trigger_id option[data-slugs]')
    ] == [
        ('action:test', 'formdef:external|formdef:other'),
        ('action:test2', 'formdef:external|formdef:other'),
    ]
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' in resp


def test_workflows_create_formdata(pub):
    create_superuser(pub)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.enable_tracking_codes = True
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
        fields.FileField(id='1', label='file', varname='foo_file'),
    ]
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')

    st1 = wf.add_status('New')
    st2 = wf.add_status('Resubmit')

    jump = st1.add_action('choice', id='_resubmit')
    jump.label = 'Resubmit'
    jump.by = ['_submitter']
    jump.status = st2.id

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
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

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st2.id))
    pq = resp.pyquery.remove_namespaces()
    assert pq('option[value="New Form Creation"]').text() == 'New Form Creation'
    assert pq('[data-id="_create_formdata"] a')[0].text == 'New Form Creation (target form)'

    resp = resp.click(href=r'^items/_create_formdata/$')
    resp.form.set('varname', 'resubmitted')
    resp = resp.form.submit(name='submit')
    resp = resp.follow()

    # checks that nothing changed after submit
    wf2 = Workflow.select()[0]
    item = wf2.get_status('2').items[0]
    assert item.varname == 'resubmitted'
    assert isinstance(item, CreateFormdataWorkflowStatusItem)
    wf.get_status('2').items[0].label = 'really resubmit'

    # duplicate
    resp = app.get('/backoffice/workflows/%s/status/%s/items/_create_formdata/' % (wf.id, st2.id))
    resp.form.set('mappings$element1$field_id', '0')
    resp = resp.form.submit(name='submit')
    pq = resp.pyquery.remove_namespaces()
    assert pq('.error').text() == 'Some destination fields are duplicated'

    # check setting map_fields_by_varname on new action
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st2.id))
    resp.forms['new-action-form']['action-formdata-action'] = 'New Form Creation'
    resp = resp.forms['new-action-form'].submit()
    resp = resp.follow()
    resp = resp.click(r'New Form Creation \(not configured\)')
    resp.form['formdef_slug'] = 'target-form'  # set target form
    resp = resp.form.submit('submit')
    assert 'Please define new mappings' in resp
    assert resp.form['map_fields_by_varname'].checked is False
    resp.form['map_fields_by_varname'].checked = True
    resp = resp.form.submit('submit')
    resp = resp.follow()
    wf = Workflow.get(wf.id)
    st2 = wf.possible_status[-1]
    assert wf.possible_status[-1].items[-1].map_fields_by_varname is True
    assert wf.possible_status[-1].items[-1].mappings is None
    assert wf.possible_status[-1].items[-1].formdef_slug == 'target-form'
    resp = app.get('/backoffice/workflows/%s/status/%s/items/%s/' % (wf.id, st2.id, st2.items[-1].id))
    resp.form['formdef_slug'] = ''  # unset target form
    resp.form.submit('submit')
    wf = Workflow.get(wf.id)
    assert wf.possible_status[-1].items[-1].formdef_slug is None
    assert wf.possible_status[-1].items[-1].map_fields_by_varname is True
    resp = app.get('/backoffice/workflows/%s/status/%s/items/%s/' % (wf.id, st2.id, st2.items[-1].id))
    resp.form['formdef_slug'] = 'target-form'  # reset target form
    resp.form.submit('submit')  # no error


def test_workflows_create_formdata_action_config(pub):
    create_superuser(pub)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = []
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')
    st = wf.add_status('New')
    st.add_action('create_formdata')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    # only mapping error: custom error message
    resp.forms[0]['formdef_slug'] = 'target-form'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' not in resp
    assert 'This action is configured in two steps. See below for details.' in resp
    assert 'Please define new mappings' in resp
    # multiple errors: do as usual
    resp.forms[0]['formdef_slug'] = 'target-form'
    resp.forms[0]['condition$type'] = 'django'
    resp.forms[0]['condition$value_django'] = '{{ 42 }}'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' in resp
    assert 'This action is configured in two steps. See below for details.' not in resp
    assert 'Please define new mappings' in resp
    assert "syntax error: Could not parse the remainder: '{{' from '{{'" in resp


def test_workflows_create_formdata_config_with_empty_values(pub):
    create_superuser(pub)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string1', varname='foo_string'),
        fields.StringField(id='1', label='string2', varname='bar_string'),
    ]
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')
    st = wf.add_status('New')
    st.add_action('create_formdata')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    # only mapping error: custom error message
    resp.forms[0]['formdef_slug'] = 'target-form'
    resp = resp.forms[0].submit('submit')
    assert 'Please define new mappings' in resp
    resp.form['map_fields_by_varname'].checked = True
    resp.form['mappings$element0$field_id'] = '1'
    resp = resp.forms[0].submit('submit')
    wf.refresh_from_storage()
    assert wf.possible_status[0].items[0].mappings[0].field_id == '1'
    assert wf.possible_status[0].items[0].mappings[0].expression is None


def test_workflows_create_formdata_deleted_field(pub):
    create_superuser(pub)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.enable_tracking_codes = True
    target_formdef.fields = [
        fields.StringField(id='0', label='string1', varname='foo'),
        fields.StringField(id='1', label='string2', varname='bar'),
    ]
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')

    st2 = wf.add_status('Resubmit')

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='{{ "a" }}'),
        Mapping(field_id='1', expression='{{ "b" }}'),
    ]

    wf.store()

    app = login(get_app(pub))
    # edit/submit to get field labels into cache
    resp = app.get('/backoffice/workflows/%s/status/%s/items/_create_formdata/' % (wf.id, st2.id))
    resp = resp.form.submit(name='submit')

    # remove field
    target_formdef.fields = [
        fields.StringField(id='1', label='string2', varname='bar'),
    ]
    target_formdef.store()

    resp = app.get('/backoffice/workflows/%s/status/%s/items/_create_formdata/' % (wf.id, st2.id))
    assert resp.form['mappings$element1$field_id'].options == [
        ('', False, '---'),
        ('1', False, 'string2 - Text (line)'),
        ('0', True, '❗ string1 (deleted field)'),
    ]


def test_workflows_create_formdata_fields_with_same_label(pub):
    create_superuser(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'Test Block'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.enable_tracking_codes = True
    target_formdef.fields = [
        fields.StringField(id='0', label='string1', varname='foo'),
        fields.StringField(id='1', label='string1', varname='bar'),
        fields.BlockField(id='2', label='block1', varname='foo2', block_slug=block.slug),
        fields.BlockField(id='3', label='block1', varname='bar2', block_slug=block.slug),
        fields.BlockField(id='4', label='block2', varname='xxx', block_slug=block.slug),
    ]
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')

    st2 = wf.add_status('Resubmit')

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='{{ "a" }}'),
        Mapping(field_id='1', expression='{{ "b" }}'),
    ]

    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/_create_formdata/' % (wf.id, st2.id))
    assert resp.form['mappings$element1$field_id'].options == [
        ('', False, '---'),
        ('0', False, 'string1 - Text (line) (foo)'),
        ('1', True, 'string1 - Text (line) (bar)'),
        ('2', False, 'block1 - Block of fields (Test Block) (foo2)'),
        ('2$123', False, 'block1 (foo2) - Test - Text (line)'),
        ('3', False, 'block1 - Block of fields (Test Block) (bar2)'),
        ('3$123', False, 'block1 (bar2) - Test - Text (line)'),
        ('4', False, 'block2 - Block of fields (Test Block)'),
        ('4$123', False, 'block2 - Test - Text (line)'),
    ]


def test_workflows_create_carddata_action_config(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = []
    carddef.store()

    Workflow.wipe()
    wf = Workflow(name='create-carddata')
    st = wf.add_status('New')
    st.add_action('create_carddata')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    # only mapping error: custom error message
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' not in resp
    assert 'This action is configured in two steps. See below for details.' in resp
    assert 'Please define new mappings' in resp
    # multiple errors: do as usual
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp.forms[0]['condition$type'] = 'django'
    resp.forms[0]['condition$value_django'] = '{{ 42 }}'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' in resp
    assert 'This action is configured in two steps. See below for details.' not in resp
    assert 'Please define new mappings' in resp
    assert "syntax error: Could not parse the remainder: '{{' from '{{'" in resp


def test_workflows_create_formdata_config_common_varnames(pub):
    create_superuser(pub)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string1', varname='foo_string'),
        fields.StringField(id='1', label='string2', varname='bar_string'),
    ]
    target_formdef.store()

    Workflow.wipe()
    wf = Workflow(name='create-formdata')
    st = wf.add_status('New')
    st.add_action('create_formdata')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['formdef_slug'] = 'target-form'
    resp = resp.forms[0].submit('submit')
    assert 'Please define new mappings' in resp
    resp.form['map_fields_by_varname'].checked = True
    resp = resp.forms[0].submit('submit')
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.pyquery('.common-varnames').length == 0

    # attach workflow to a formdef
    formdef = FormDef()
    formdef.name = 'form'
    formdef.workflow = wf
    formdef.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.pyquery('.common-varnames').text() == 'Common varnames: none'

    formdef.fields = [
        fields.StringField(id='2', label='string1', varname='foo_string'),
    ]
    formdef.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.pyquery('.common-varnames').text() == 'Common varnames: foo_string'


def test_workflows_create_formdata_expression_types(pub):
    create_superuser(pub)

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = []
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    st1 = wf.add_status('New')
    create_formdata = st1.add_action('create_formdata')
    create_formdata.formdef_slug = target_formdef.url_name
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st1.id))
    assert 'mappings$element0$expression$type' not in resp.text


def test_workflows_edit_carddata_action_config(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = []
    carddef.store()

    Workflow.wipe()
    wf = Workflow(name='edit-carddata')
    st = wf.add_status('New')
    st.add_action('edit_carddata')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    # only mapping error: custom error message
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' not in resp
    assert 'This action is configured in two steps. See below for details.' in resp
    assert 'Please define new mappings' in resp
    # multiple errors: do as usual
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp.forms[0]['condition$type'] = 'django'
    resp.forms[0]['condition$value_django'] = '{{ 42 }}'
    resp = resp.forms[0].submit('submit')
    assert 'There were errors processing your form.  See below for details.' in resp
    assert 'This action is configured in two steps. See below for details.' not in resp
    assert 'Please define new mappings' in resp
    assert "syntax error: Could not parse the remainder: '{{' from '{{'" in resp


def test_workflows_edit_anonymise_action(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    status = workflow.add_status(name='baz')
    action = status.add_action('anonymise')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(action.get_admin_url())
    assert [x[0] for x in resp.form['mode'].options] == ['final', 'intermediate', 'unlink_user']
    resp = app.get(status.get_admin_url())
    assert resp.pyquery('#items-list a.biglistitem--content').text() == 'Anonymisation (final)'

    pub.site_options.set('options', 'enable-intermediate-anonymisation', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(action.get_admin_url())
    assert [x[0] for x in resp.form['mode'].options] == ['final', 'unlink_user']
    resp = app.get(status.get_admin_url())
    assert resp.pyquery('#items-list a.biglistitem--content').text() == 'Anonymisation'


def test_workflows_edit_carddata_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    CardDef.wipe()

    wf = Workflow(name='edit card')
    st = wf.add_status('Update card', 'st')
    wf.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    carddef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st.id))
    assert 'Edit Card Data' in [o[0] for o in resp.forms[0]['action-formdata-action'].options]

    resp.forms[0]['action-formdata-action'] = 'Edit Card Data'
    resp = resp.forms[0].submit().follow()
    assert 'Edit Card Data (not configured)' in resp.text

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp = resp.forms[0].submit('submit')
    assert 'Leaving the field blank will empty the value.' in resp.text
    resp.forms[0]['mappings$element0$field_id'] = '1'
    resp = resp.forms[0].submit('submit').follow()
    assert 'Edit Card Data (not configured)' not in resp.text
    assert Workflow.get(wf.id).possible_status[0].items[0].target_mode == 'all'
    assert Workflow.get(wf.id).possible_status[0].items[0].target_id is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['target_mode'] = 'manual'
    resp.forms[0]['target_id$value_template'] = '{{ form_var_plop_id }}'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].target_mode == 'manual'
    assert Workflow.get(wf.id).possible_status[0].items[0].target_id == '{{ form_var_plop_id }}'


def test_workflows_assign_carddata_action_options(pub):
    create_superuser(pub)

    Workflow.wipe()
    wf = Workflow(name='assign card')
    st = wf.add_status('New')
    st.add_action('assign_carddata')
    wf.store()

    app = login(get_app(pub))

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert 'target_id$type' not in resp.text
    assert 'user_association_template$type' not in resp.text
    assert resp.pyquery('[name="condition$type"]').val() == 'django'
    assert resp.pyquery('[name="condition$type"]').attr.type == 'hidden'


def test_workflows_assign_carddata_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    CardDef.wipe()

    wf = Workflow(name='assign card')
    st = wf.add_status('Update card', 'st')
    wf.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    carddef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st.id))
    assert 'Assign Card Data' in [o[0] for o in resp.forms[0]['action-formdata-action'].options]

    resp.forms[0]['action-formdata-action'] = 'Assign Card Data'
    resp = resp.forms[0].submit().follow()
    assert 'Assign Card Data (not configured)' in resp.text

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['formdef_slug'] = 'my-card'
    resp = resp.forms[0].submit('submit')
    assert 'Assign Card Data (not configured)' not in resp.text
    assert Workflow.get(wf.id).possible_status[0].items[0].target_mode == 'all'
    assert Workflow.get(wf.id).possible_status[0].items[0].target_id is None
    assert Workflow.get(wf.id).possible_status[0].items[0].user_association_mode is None
    assert Workflow.get(wf.id).possible_status[0].items[0].user_association_template is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['target_mode'] = 'manual'
    resp.forms[0]['target_id$value_template'] = '{{ form_var_plop_id }}'
    resp.forms[0]['user_association_mode'] = 'keep-user'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].target_mode == 'manual'
    assert Workflow.get(wf.id).possible_status[0].items[0].target_id == '{{ form_var_plop_id }}'
    assert Workflow.get(wf.id).possible_status[0].items[0].user_association_mode == 'keep-user'
    assert Workflow.get(wf.id).possible_status[0].items[0].user_association_template is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['user_association_mode'] = 'custom'
    resp.forms[0]['user_association_template$value_template'] = '{{ form_var_user_id }}'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].user_association_mode == 'custom'
    assert (
        Workflow.get(wf.id).possible_status[0].items[0].user_association_template == '{{ form_var_user_id }}'
    )


def test_workflows_user_notification_action(pub):
    create_superuser(pub)
    Workflow.wipe()

    wf = Workflow(name='notif')
    st = wf.add_status('New', 'st')
    wf.store()

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://www.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/status/%s/' % (wf.id, st.id))
    assert 'User Notification' in [o[0] for o in resp.forms[0]['action-interaction'].options]

    resp.forms[0]['action-interaction'] = 'User Notification'
    resp = resp.forms[0].submit().follow()
    assert 'User Notification' in resp.text
    assert Workflow.get(wf.id).possible_status[0].items[0].to == ['_submitter']
    assert Workflow.get(wf.id).possible_status[0].items[0].users_template is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.forms[0]['to'].value == '_submitter'
    resp.forms[0]['to'] = '_receiver'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].to == ['_receiver']
    assert Workflow.get(wf.id).possible_status[0].items[0].users_template is None

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.forms[0]['to'].value == '_receiver'
    resp.forms[0]['to'] = '__other'
    resp.forms[0]['users_template$value_template'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].to == []
    assert Workflow.get(wf.id).possible_status[0].items[0].users_template == 'foobar'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.forms[0]['to'].value == '__other'
    assert resp.forms[0]['users_template$value_template'].value == 'foobar'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.forms[0]['target_url'] = '{{portal_url}}'
    resp = resp.forms[0].submit('submit')
    assert Workflow.get(wf.id).possible_status[0].items[0].target_url == '{{portal_url}}'
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.forms[0]['target_url'].value == '{{portal_url}}'


def test_workflows_criticality_levels(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add criticality level')
    resp = resp.forms[0].submit('cancel')
    assert not Workflow.get(workflow.id).criticality_levels

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add criticality level')
    resp.forms[0]['name'] = 'vigilance'
    resp = resp.forms[0].submit('submit')
    assert len(Workflow.get(workflow.id).criticality_levels) == 1
    assert Workflow.get(workflow.id).criticality_levels[0].name == 'vigilance'

    # test rename
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('vigilance')
    resp = resp.forms[0].submit('cancel')

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('vigilance')
    resp.forms[0]['name'] = 'Vigilance'
    resp = resp.forms[0].submit('submit')
    assert len(Workflow.get(workflow.id).criticality_levels) == 1
    assert Workflow.get(workflow.id).criticality_levels[0].name == 'Vigilance'

    # add a second level
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('add criticality level')
    resp.forms[0]['name'] = 'Alerte attentat'
    resp = resp.forms[0].submit('submit')
    assert len(Workflow.get(workflow.id).criticality_levels) == 2
    assert Workflow.get(workflow.id).criticality_levels[0].name == 'Vigilance'
    assert Workflow.get(workflow.id).criticality_levels[1].name == 'Alerte attentat'

    # test removal
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('Vigilance')
    resp = resp.forms[0].submit('delete-level')
    assert len(Workflow.get(workflow.id).criticality_levels) == 1


def test_workflows_order_criticality_levels(pub):
    Workflow.wipe()
    workflow = Workflow(name='Foobarbaz')
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='blue'),
        WorkflowCriticalityLevel(name='red'),
    ]
    workflow.store()

    create_superuser(pub)
    app = login(get_app(pub))

    level1_id = Workflow.get(workflow.id).criticality_levels[0].id
    level2_id = Workflow.get(workflow.id).criticality_levels[1].id
    level3_id = Workflow.get(workflow.id).criticality_levels[2].id
    app.get(
        '/backoffice/workflows/%s/update_criticality_levels_order?order=%s;%s;%s;'
        % (workflow.id, level1_id, level2_id, level3_id)
    )
    assert Workflow.get(workflow.id).criticality_levels[0].id == level1_id
    assert Workflow.get(workflow.id).criticality_levels[1].id == level2_id
    assert Workflow.get(workflow.id).criticality_levels[2].id == level3_id
    app.get(
        '/backoffice/workflows/%s/update_criticality_levels_order?order=%s;%s;%s;'
        % (workflow.id, level3_id, level2_id, level1_id)
    )
    assert Workflow.get(workflow.id).criticality_levels[0].id == level3_id
    assert Workflow.get(workflow.id).criticality_levels[1].id == level2_id
    assert Workflow.get(workflow.id).criticality_levels[2].id == level1_id

    # unknown id: ignored
    app.get(
        '/backoffice/workflows/%s/update_criticality_levels_order?order=%s;%s;%s;0'
        % (workflow.id, level2_id, level3_id, level1_id)
    )
    assert Workflow.get(workflow.id).criticality_levels[0].id == level2_id
    assert Workflow.get(workflow.id).criticality_levels[1].id == level3_id
    assert Workflow.get(workflow.id).criticality_levels[2].id == level1_id

    # missing id: do nothing
    app.get(
        '/backoffice/workflows/%s/update_criticality_levels_order?order=%s;%s;'
        % (workflow.id, level3_id, level2_id)
    )
    assert Workflow.get(workflow.id).criticality_levels[0].id == level2_id
    assert Workflow.get(workflow.id).criticality_levels[1].id == level3_id
    assert Workflow.get(workflow.id).criticality_levels[2].id == level1_id


def test_workflows_wscall_label(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    wscall = baz_status.add_action('webservice_call')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, baz_status.id))
    assert 'Webservice' in resp.text
    assert 'Webservice (' not in resp.text

    wscall.label = 'foowscallbar'
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, baz_status.id))
    assert 'Webservice (foowscallbar)' in resp.text


@pytest.mark.parametrize('value', [True, False])
def test_workflows_wscall_options(pub, value):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    baz_status.add_action('webservice_call')
    workflow.store()

    pub.cfg['debug'] = {}
    pub.write_cfg()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    assert 'notify_on_errors' not in resp.form.fields

    pub.cfg['debug'] = {'error_email': 'test@localhost'}
    pub.write_cfg()
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    assert resp.form['notify_on_errors'].value is None
    assert resp.form['record_on_errors'].value == 'yes'
    resp.form['notify_on_errors'] = value
    resp.form['record_on_errors'] = value
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    assert resp.form['notify_on_errors'].value == ('yes' if value else None)
    assert resp.form['record_on_errors'].value == ('yes' if value else None)
    resp.form['notify_on_errors'] = not value
    resp.form['record_on_errors'] = not value
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    assert resp.form['notify_on_errors'].value == ('yes' if not value else None)
    assert resp.form['record_on_errors'].value == ('yes' if not value else None)


def test_workflows_wscall_status_error(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    foo_status = workflow.add_status(name='foo')
    wscall = baz_status.add_action('webservice_call')
    wscall.action_on_app_error = foo_status.id
    wscall.action_on_4xx = foo_status.id
    wscall.action_on_5xx = foo_status.id
    wscall.action_on_bad_data = foo_status.id
    wscall.action_on_network_errors = foo_status.id
    workflow.store()

    app = login(get_app(pub))
    app.get('/backoffice/workflows/%s/' % workflow.id)
    assert LoggedError.count() == 0

    # delete foo status, make sure rendering the graph doesn't record errors
    del workflow.possible_status[1]
    workflow.store()
    app.get('/backoffice/workflows/%s/' % workflow.id)
    assert LoggedError.count() == 0


def test_workflows_wscall_empty_param_values(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    baz_status.add_action('webservice_call')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (workflow.id, baz_status.id))
    resp.form['qs_data$element0key'] = 'foo'
    resp.form['post_data$element0key'] = 'bar'
    resp = resp.form.submit('submit').follow()

    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].qs_data == {'foo': ''}
    assert workflow.possible_status[0].items[0].post_data == {'bar': ''}


def test_workflows_form_action_config(pub):
    create_superuser(pub)

    Workflow.wipe()
    wf = Workflow(name='foo')
    st = wf.add_status('New')
    form = st.add_action('form')
    wf.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    resp.form['by$element0'] = '_submitter'
    resp.form['varname'] = 'myform'
    resp.form['condition$type'] = 'django'
    resp.form['condition$value_django'] = '42'
    assert 'Edit Fields' not in resp.text
    assert resp.form.fields['submit'][0]._value == 'Submit and go to fields edition'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/%s/status/%s/items/1/fields/' % (
        wf.id,
        st.id,
    )
    resp = resp.follow()
    resp.form['label'] = 'Text field'
    resp = resp.form.submit('submit')

    wf = Workflow.get(wf.id)
    form = wf.possible_status[0].items[0]
    assert form.by == ['_submitter']
    assert form.varname == 'myform'
    assert form.condition == {'type': 'django', 'value': '42'}
    assert form.formdef.fields[0].label == 'Text field'

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert 'Edit Fields' in resp.text
    resp = resp.click('Edit Fields')

    resp = app.get('/backoffice/workflows/%s/status/%s/items/1/' % (wf.id, st.id))
    assert resp.form.fields['submit'][0]._value == 'Submit'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/workflows/%s/status/%s/items/' % (
        wf.id,
        st.id,
    )


def test_workflows_inspect_view(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='foo')

    workflow.criticality_levels = [WorkflowCriticalityLevel(name='green')]

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(
            id='bo1', label='1st backoffice field', varname='backoffice_blah', required='required'
        ),
    ]

    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(fields.StringField(label='Test'))

    foo_status = workflow.add_status(name='foo')
    jump = foo_status.add_action('choice')
    jump.backoffice_info_text = '<p>Hello</p>'

    baz_status = workflow.add_status(name='baz')
    baz_status.backoffice_info_text = 'Info text'
    wscall = baz_status.add_action('webservice_call')
    wscall.post_data = {'foo1': 'bar1'}
    wscall.qs_data = {}

    wscall2 = foo_status.add_action('webservice_call')
    wscall2.qs_data = {}
    wscall2.response_type = 'attachment'

    dispatch1 = baz_status.add_action('dispatch')
    dispatch1.dispatch_type = 'automatic'
    dispatch1.rules = [
        {'role_id': role.id, 'value': 'foo'},
    ]
    dispatch2 = baz_status.add_action('dispatch')
    dispatch2.dispatch_type = 'manual'
    dispatch2.role_key = '_receiver'
    dispatch2.role_id = role.id

    baz_status.backoffice_info_text = '<p>Hello</p>'

    display_form = baz_status.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(fields.StringField(label='Test'))
    display_form.formdef.fields.append(fields.StringField(label='Test2'))
    display_form.formdef.fields[1].maxlength = '40'
    display_form.formdef.fields[1].condition = {'type': 'django', 'value': 'False'}
    display_form.backoffice_info_text = '<p>Foo</p>'

    jump = baz_status.add_action('jump', id='_jump')
    jump.timeout = 86400
    jump.mode = 'timeout'
    jump.status = foo_status.id
    jump.condition = {'type': 'django', 'value': '1 == 1'}

    ac1 = workflow.add_global_action('Action', 'ac1')
    ac1.backoffice_info_text = '<p>Foo</p>'

    add_to_journal = ac1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'

    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = [role.id]

    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert '2 statuses.' in resp.text
    assert (
        '<li class="parameter-backoffice_info_text"><span class="parameter">Information Text for Backoffice:</span> <p>Hello</p></li>'
        in resp.text
    )
    assert (
        '<li class="parameter-dispatch_type"><span class="parameter">Dispatch Type:</span> Multiple</li>'
        '<li class="parameter-rules"><span class="parameter">Rules:</span> '
        '<ul class="rules"><li>foo → foobar</li></ul>'
        '</li>'
    ) in resp.text
    assert (
        '<li class="parameter-dispatch_type"><span class="parameter">Dispatch Type:</span> Simple</li>'
        '<li class="parameter-role_id"><span class="parameter">Role:</span> foobar</li>'
    ) in resp.text
    assert '<tt>1 == 1</tt>' in resp.text
    assert resp.pyquery('.inspect-wf-form-fields li:last-child li.parameter-label').text() == 'Label: Test2'
    assert (
        resp.pyquery('.inspect-wf-form-fields li:last-child li.parameter-maxlength').text()
        == 'Maximum number of characters: 40'
    )
    assert (
        resp.pyquery('.inspect-wf-form-fields li:last-child li.parameter-condition').text()
        == 'Display Condition: False (Django)'
    )

    # wscall
    assert (
        resp.pyquery(f'#status-{baz_status.id} ~ ul .parameter-post_data').text() == 'POST data:\nfoo1 → bar1'
    )
    assert (
        resp.pyquery(f'#status-{baz_status.id} ~ ul .parameter-qs_data').text() == 'Query string data: none'
    )

    # create workflow with all action types (unconfigured)
    workflow = Workflow(name='bar')
    status = workflow.add_status('plop')
    for action_type in item_classes:
        status.add_action(action_type.key)
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)

    # check final status are noted
    workflow = Workflow(name='bar2')
    status1 = workflow.add_status('statusfoo')
    status1.forced_endpoint = True
    status2 = workflow.add_status('statusbar')
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert (
        resp.pyquery(f'#status-{status1.id} + p').text()
        == 'This status has been manually set to be considered as terminal.'
    )
    assert (
        resp.pyquery(f'#status-{status2.id} + p').text()
        == 'This status has been automatically evaluated as being terminal.'
    )


def test_workflows_inspect_view_dispatch_action(pub):
    create_superuser(pub)
    pub.role_class.wipe()

    role = pub.role_class(name='foobar')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='foo')
    status = workflow.add_status('plop')
    action = status.add_action('dispatch')
    action.dispatch_type = 'manual'
    action.role_id = role.id
    workflow.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert resp.pyquery('.parameter-role_id').text() == 'Role: foobar'

    action.dispatch_type = 'automatic'
    action.rules = [
        {'role_id': role.id, 'value': 'foo'},
    ]
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert resp.pyquery('.rules').text() == 'foo → foobar'

    # missing role
    pub.role_class.wipe()
    action.dispatch_type = 'manual'
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert resp.pyquery('.parameter-role_id').text() == 'Role: Unknown role (%s)' % role.id

    action.dispatch_type = 'automatic'
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert resp.pyquery('.rules').text() == f'foo → Unknown role ({role.id})'


def test_workflows_unused(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    assert 'Unused' not in resp.text

    workflow = Workflow(name='Workflow One')
    workflow.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Unused' in resp.text

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Unused' in resp.text

    formdef.workflow = workflow
    formdef.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Unused' not in resp.text

    workflow = Workflow(name='Workflow Two')
    workflow.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Unused' in resp.text


def test_workflows_categories_in_index(pub):
    create_superuser(pub)
    FormDef.wipe()
    Workflow.wipe()
    WorkflowCategory.wipe()

    wf1 = Workflow(name='wf1')
    wf1.store()
    wf2 = Workflow(name='wf2')
    wf2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    assert 'Uncategorised' not in resp.text

    cat = WorkflowCategory(name='XcategoryY')
    cat.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Uncategorised' in resp.text
    assert 'XcategoryY' not in resp.text

    wf2.category_id = cat.id
    wf2.store()
    resp = app.get('/backoffice/workflows/')
    assert 'Uncategorised' in resp.text
    assert 'XcategoryY' in resp.text


def test_workflow_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/workflows/', status=403)

    WorkflowCategory.wipe()
    cat = WorkflowCategory(name='Foo')
    cat.store()

    Workflow.wipe()
    workflow = Workflow()
    workflow.name = 'workflow title'
    workflow.category_id = cat.id
    workflow.store()

    cat = WorkflowCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/workflows/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'workflow title' not in resp.text  # workflow in that category
    assert 'Bar' not in resp.text  # not yet any form in this category

    resp = resp.click('New Workflow')
    resp.forms[0]['name'] = 'workflow in category'
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user
    resp = resp.forms[0].submit().follow()

    # check category select only let choose one
    resp = resp.click(href='category')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user

    resp = app.get('/backoffice/workflows/')
    assert 'Bar' in resp.text  # now there's a form in this category
    assert 'workflow in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    assert 'href="data-sources/"' not in resp.text
    assert 'href="mail-templates/"' not in resp.text
    app.get('/backoffice/workflows/categories/', status=403)
    app.get('/backoffice/workflows/data-sources/', status=403)
    app.get('/backoffice/workflows/mail-templates/', status=403)
    app.get('/backoffice/workflows/%s/' % workflow.id, status=403)

    # no import into other category
    workflow_xml = ET.tostring(workflow.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('workflow.wcs', workflow_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    # access to default workflows
    app.get('/backoffice/workflows/_carddef_default/')
    resp = app.get('/backoffice/workflows/_default/')

    # duplicate on default workflows should have the category field
    resp = resp.click(href='duplicate')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    resp = resp.forms[0].submit('cancel').follow()
    resp = resp.click(href='duplicate')
    resp = resp.forms[0].submit('submit').follow()
    assert Workflow.get(3).name == 'Default (copy)'
    assert Workflow.get(3).category_id == cat.id


def test_workflow_restricted_access_import_error(pub, backoffice_user, backoffice_role):
    WorkflowCategory.wipe()
    cat = WorkflowCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()
    Workflow.wipe()

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/workflows/import')
    resp.forms[0]['file'] = Upload('workflow.wcs', b'broken content')
    resp = resp.forms[0].submit()
    assert 'Invalid File' in resp.text
    assert Workflow.count() == 0


def test_workflows_site_disabled_action(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('baz')
    assert 'Alert' in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'Comment' in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'Role Removal' in [x[0] for x in resp.forms[0]['action-user-action'].options]

    pub.site_options.set('options', 'disabled-workflow-actions', 'commentable, remove_role')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    create_superuser(pub)

    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    resp = resp.click('baz')
    assert 'Alert' in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'Comment' not in [x[0] for x in resp.forms[0]['action-interaction'].options]
    assert 'Role Removal' not in [x[0] for x in resp.forms[0]['action-user-action'].options]


def test_workflows_duplicate_with_create_document_action(pub):
    create_superuser(pub)
    Workflow.wipe()

    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/status/1/')

    resp.forms[0]['action-interaction'] = 'Document Creation'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Document Creation')
    resp.form['model_file'] = Upload('test.xml', b'<t>Model content</t>')
    resp = resp.form.submit('submit').follow().follow()
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'

    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Duplicate')
    resp.form['name'].value = 'create doc 2'
    resp = resp.form.submit('submit').follow()

    resp = app.get('/backoffice/workflows/2/status/1/')
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'

    # modify file in initial action
    resp = app.get('/backoffice/workflows/1/status/1/')
    resp = resp.click('Document Creation')
    resp.form['model_file'] = Upload('test2.xml', b'<t>Something else</t>')
    resp = resp.form.submit('submit').follow().follow()

    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test2.xml')
    assert resp_model_content.body == b'<t>Something else</t>'

    # check file is not changed in the duplicated action
    resp = app.get('/backoffice/workflows/2/status/1/')
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.xml')
    assert resp_model_content.body == b'<t>Model content</t>'


def test_workflows_duplicate_keep_ids(pub):
    create_superuser(pub)
    Workflow.wipe()

    workflow = Workflow(name='foo')
    workflow.add_status(name='baz', id='baz')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1-1x', label='bo field 1'),
    ]
    ac1 = workflow.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('timeout')
    trigger.anchor_expression = 'False'
    trigger.anchor_template = '{{x}}'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('Duplicate')
    resp.form['name'].value = 'foo 2'
    resp = resp.form.submit('submit').follow()

    wf2 = Workflow.get(2)
    assert wf2.possible_status[0].id == 'baz'
    assert wf2.backoffice_fields_formdef.fields[0].id == 'bo1-1x'
    assert wf2.global_actions[0].id == ac1.id
    assert wf2.global_actions[0].triggers[-1].id == trigger.id


def test_remove_tracking_code_details(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    remove_code = baz_status.add_action('remove_tracking_code')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, baz_status.id))
    assert 'Remove Tracking Code' in resp.text
    assert 'Remove Tracking Code (' not in resp.text

    remove_code.replace = True
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/status/%s/' % (workflow.id, baz_status.id))
    assert 'Remove Tracking Code (replace with a new one)' in resp.text


def test_workflow_backoffice_field_statistics_data_update(pub):
    create_superuser(pub)

    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [fields.BoolField(id='1', label='Bool', varname='bool')]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow = workflow
    formdef.store()

    app = login(get_app(pub))

    formdata = formdef.data_class()()
    formdata.data['1'] = True
    formdata.store()

    assert 'bool' not in formdata.statistics_data

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/backoffice-fields/fields/1/')

    resp.form['display_locations$element2'] = True
    resp = resp.form.submit('submit').follow()
    assert 'Statistics data will be collected in the background.' in resp.text

    formdata.refresh_from_storage()
    assert formdata.statistics_data['bool'] == [True]


def test_workflows_edit_aggregationemail_action(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st = workflow.add_status(name='baz')
    item = st.add_action('aggregationemail')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(item.get_admin_url())
    assert '_submitter' not in [x[0] for x in resp.form['to$element0'].options]


def test_workflows_function_and_role_with_same_name(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role1 = pub.role_class(name='Foo')
    role1.store()
    role2 = pub.role_class(name='Foobar')
    role2.store()
    Workflow.wipe()

    workflow = Workflow(name='foo')
    workflow.roles = {'_receiver': 'Receiver', '_foobar': 'Foobar'}
    st1 = workflow.add_status(name='baz')
    commentable = st1.add_action('commentable', id='_commentable')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(commentable.get_admin_url())
    assert resp.form['by$element0'].options == [
        ('None', True, '---'),
        ('_submitter', False, 'User'),
        ('_receiver', False, 'Receiver'),
        ('_foobar', False, 'Foobar'),
        ('logged-users', False, 'Logged Users'),
        ('', False, '----'),
        (str(role1.id), False, 'Foo'),
        (str(role2.id), False, 'Foobar [role]'),  # same name as function -> role suffix
    ]


def test_workflow_test_results(pub):
    create_superuser(pub)
    AfterJob.wipe()
    TestDef.wipe()
    TestResults.wipe()

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'test title'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/edit')
    resp.form['name'] = 'test'
    resp = resp.form.submit('submit').follow()
    assert TestResults.count() == 0
    assert 'failed' not in [x.status for x in AfterJob.select()]

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    resp = app.get('/backoffice/workflows/1/edit')
    resp.form['name'] = 'test 2'
    resp = resp.form.submit('submit').follow()
    assert TestResults.count() == 0

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(id='1'),
    ]
    testdef.store()

    resp = app.get('/backoffice/workflows/1/edit')
    resp.form['name'] = 'test 3'
    resp = resp.form.submit('submit').follow()

    assert TestResults.count() == 1
    result = TestResults.select()[0]
    assert result.reason == 'Change in workflow'

    resp = resp.click('add status')
    resp.forms[0]['name'] = 'new status'
    resp = resp.forms[0].submit()

    # same result -> no new result saved
    assert TestResults.count() == 1


def test_workflow_documentation(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    status = workflow.add_status(name='New status')
    status.add_action('anonymise')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo234', label='bo field 1'),
    ]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.StringField(id='va123', label='bo field 1'),
    ]
    global_action = workflow.add_global_action('action1')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(workflow.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    assert app.post_json(workflow.get_admin_url() + 'update-documentation', {}).json.get('err') == 1
    resp = app.post_json(workflow.get_admin_url() + 'update-documentation', {'content': ''})
    assert resp.json == {'err': 0, 'empty': True, 'changed': False}
    resp = app.post_json(workflow.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.documentation == '<p>doc</p>'

    # check forbidden HTML is cleaned
    resp = app.post_json(
        workflow.get_admin_url() + 'update-documentation',
        {'content': '<p>iframe</p><iframe src="xx"></iframe>'},
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.documentation == '<p>iframe</p>'

    resp = app.get(workflow.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(workflow.get_admin_url() + 'variables/fields/')
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(
        workflow.get_admin_url() + 'variables/fields/update-documentation', {'content': '<p>doc</p>'}
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.variables_formdef.documentation == '<p>doc</p>'
    resp = app.get(workflow.get_admin_url() + 'variables/fields/')
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(workflow.get_admin_url() + 'variables/fields/va123/')
    assert resp.pyquery('.documentation[hidden]')
    assert resp.pyquery('#sidebar[hidden]')
    resp = app.post_json(
        workflow.get_admin_url() + 'variables/fields/va123/update-documentation', {'content': '<p>doc</p>'}
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.variables_formdef.fields[0].documentation == '<p>doc</p>'
    resp = app.get(workflow.get_admin_url() + 'variables/fields/va123/')
    assert resp.pyquery('.documentation:not([hidden])')
    assert resp.pyquery('#sidebar:not([hidden])')

    resp = app.get(workflow.get_admin_url() + 'backoffice-fields/fields/')
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(
        workflow.get_admin_url() + 'backoffice-fields/fields/update-documentation', {'content': '<p>doc</p>'}
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.backoffice_fields_formdef.documentation == '<p>doc</p>'
    resp = app.get(workflow.get_admin_url() + 'backoffice-fields/fields/')
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(workflow.get_admin_url() + 'backoffice-fields/fields/bo234/')
    assert resp.pyquery('.documentation[hidden]')
    assert resp.pyquery('#sidebar[hidden]')
    resp = app.post_json(
        workflow.get_admin_url() + 'backoffice-fields/fields/bo234/update-documentation',
        {'content': '<p>doc</p>'},
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.backoffice_fields_formdef.fields[0].documentation == '<p>doc</p>'
    resp = app.get(workflow.get_admin_url() + 'backoffice-fields/fields/bo234/')
    assert resp.pyquery('.documentation:not([hidden])')
    assert resp.pyquery('#sidebar:not([hidden])')

    resp = app.get(global_action.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(global_action.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.global_actions[0].documentation == '<p>doc</p>'
    resp = app.get(global_action.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(status.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(status.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].documentation == '<p>doc</p>'
    resp = app.get(status.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')

    resp = app.get(workflow.get_admin_url() + 'inspect')
    assert resp.pyquery('.documentation').length == 5


def test_workflow_action_condition_common_varnames(pub):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    create_superuser(pub)

    workflow = Workflow(name='wf')
    status = workflow.add_status('st1')
    action = status.add_action('sendmail')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='123', label='hello', varname='hello')]
    formdef.store()

    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='123', label='hello', varname='nothello')]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get(action.get_admin_url())
    assert set(json.loads(resp.pyquery('#common-varnames').text())) == set()

    formdef.workflow = workflow
    formdef.store()
    resp = app.get(action.get_admin_url())
    assert set(json.loads(resp.pyquery('#common-varnames').text())) == {'hello'}

    carddef.workflow = workflow
    carddef.store()
    resp = app.get(action.get_admin_url())
    assert set(json.loads(resp.pyquery('#common-varnames').text())) == set()

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1-1x', label='bo field 1', varname='world'),
    ]
    workflow.store()
    resp = app.get(action.get_admin_url())
    assert set(json.loads(resp.pyquery('#common-varnames').text())) == {'world'}


def test_workflows_by_slug(pub):
    Workflow.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    workflow = Workflow()
    workflow.name = 'workflow title'
    workflow.store()

    assert app.get('/backoffice/workflows/by-slug/workflow-title').location == workflow.get_admin_url()
    assert app.get('/backoffice/workflows/by-slug/xxx', status=404)
