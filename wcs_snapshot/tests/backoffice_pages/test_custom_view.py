import json
import os
import random
import re
import string

import pytest
from quixote import get_publisher

from wcs import fields
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser, create_user


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
    [api-secrets]
    coucou = 1234
    '''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_custom_view(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.data = {}
        if i == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'
        formdata.jump_status('new')
        formdata.store()

    other_formdef = FormDef()
    other_formdef.workflow_roles = {'_receiver': 1}
    other_formdef.name = 'other form'
    other_formdef.fields = []
    other_formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<span>User Label</span>') == 1
    assert resp.text.count('<tr') == 4

    # create a view for all, with the same slug
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    # columns
    resp.forms['listing-settings']['user-label'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    # filters
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter-1-value'] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<tr') == 3

    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<tr') == 3
    assert resp.pyquery('.sidebar-custom-views li.active a').attr['href'] == '../user-custom-test-view/'

    resp = app.get('/backoffice/management/form-title/custom-test-view/')
    assert resp.text.count('<tr') == 4
    assert resp.pyquery('.sidebar-custom-views li.active a').attr['href'] == '../custom-test-view/'

    resp.forms['save-custom-view']['_form_id'] = 'xxxx'  # invalid csrf token
    resp = resp.forms['save-custom-view'].submit().follow()
    assert 'Invalid form.' in resp.text

    resp = app.get('/backoffice/management/form-title/user-custom-test-view/')
    resp.forms['listing-settings']['filter-1-value'] = 'foo'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 2
    assert resp.forms['save-custom-view']['update'].checked is True
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.text.count('<tr') == 2

    resp.forms['listing-settings']['filter-1-value'] = 'foo'
    resp.forms['listing-settings']['filter-1-operator'] = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 3
    assert resp.forms['save-custom-view']['update'].checked is True
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.text.count('<tr') == 3

    custom_view.remove_self()
    resp = app.get('/backoffice/management/other-form/')
    assert 'custom test view' not in resp

    # check it's not possible to create a view without any columns
    for field_key in resp.forms['listing-settings'].fields:
        if not field_key:
            continue
        if field_key.startswith('filter'):
            continue
        if resp.forms['listing-settings'][field_key].attrs.get('type') != 'checkbox':
            continue
        resp.forms['listing-settings'][field_key].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp = resp.forms['save-custom-view'].submit().follow()
    assert 'Views must have at least one column.' in resp.text

    resp.forms['save-custom-view']['title'] = ''
    resp = resp.forms['save-custom-view'].submit().follow()
    assert 'Missing title.' in resp.text


def test_backoffice_custom_view_unknown_filter(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(10):
        formdata = formdef.data_class()()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<tr') == 10 + 1

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-42': 'on', 'filter-42-value': 'foo'}
    custom_view.store()

    resp = app.get('/backoffice/management/form-title/custom-test-view/')
    assert resp.text.count('<tr') == 1
    assert resp.pyquery('#messages').text() == 'Invalid filter "42".'


def test_backoffice_custom_view_bad_filter_type(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field',
            items=['foo', 'bar', 'baz'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.data = {}
        if i == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<tr') == 3 + 1

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-1-value': ['', 'foo'],
        'filter-1': 'on',
    }
    custom_view.store()

    resp = app.get('/backoffice/management/form-title/custom-test-view/')
    assert resp.text.count('<tr') == 1


def test_backoffice_custom_view_user_filter(pub):
    superuser = create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    user1 = pub.user_class(name='userA')
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.store()

    formdef.data_class().wipe()
    for i in range(10):
        formdata = formdef.data_class()()
        formdata.data = {'1': 'foo', '1_display': 'foo'}
        if i < 1:
            formdata.user_id = user1.id
        elif i < 3:
            formdata.user_id = user2.id
        else:
            formdata.user_id = superuser.id
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<tr') == 10 + 1

    # No value selected, no filtering
    resp.forms['listing-settings']['filter-user'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-user-value'] = ''
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 10 + 1
    assert resp.text.count('"cell-user">userA<') == 1
    assert resp.text.count('"cell-user">userB<') == 2
    assert resp.text.count('"cell-user">admin<') == 7
    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 10 + 1
    assert resp.text.count('"cell-user">userA<') == 1
    assert resp.text.count('"cell-user">userB<') == 2
    assert resp.text.count('"cell-user">admin<') == 7

    # filter on current user
    resp.forms['listing-settings']['filter-user-value'] = '__current__'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 7 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 7
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 7 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 7

    # filter on userA
    resp = app.get(
        '/backoffice/management/form-title/user-custom-test-view/?filter-user=on&filter-user-value=%s'
        % user1.id
    )
    assert resp.text.count('<tr') == 1 + 1
    assert resp.text.count('"cell-user">userA<') == 1
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0
    assert '<option value="%s" selected="selected">userA</option>' % user1.id in resp
    resp = resp.forms['listing-settings'].submit().follow()
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    assert resp.text.count('"cell-user">userA<') == 1
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0

    # filter on unknown
    resp = app.get(
        '/backoffice/management/form-title/user-custom-test-view/?filter-user=on&filter-user-value=unknown'
    )
    assert resp.text.count('<tr') == 0 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0
    assert '<option value="unknown" selected="selected">Unknown</option>' in resp
    resp = resp.forms['listing-settings'].submit().follow()
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 0 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0

    # filter on uuid - userB
    user2.name_identifiers = ['0123456789']
    user2.store()
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/?filter-user-uuid=0123456789')
    assert resp.text.count('<tr') == 2 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 2
    assert resp.text.count('"cell-user">admin<') == 0
    assert '<option value="%s" selected="selected">userB</option>' % user2.id in resp
    resp = resp.forms['listing-settings'].submit().follow()
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 2 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 2
    assert resp.text.count('"cell-user">admin<') == 0

    # filter on uuid - current
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/?filter-user-uuid=__current__')
    assert resp.text.count('<tr') == 7 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 7
    assert '<option value="__current__" selected="selected">Current user</option>' in resp
    resp = resp.forms['listing-settings'].submit().follow()
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 7 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 7

    # filter on uuid - unknown
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/?filter-user-uuid=unknown')
    assert resp.text.count('<tr') == 0 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0
    assert '<option value="-1" selected="selected">Unknown</option>' in resp
    resp = resp.forms['listing-settings'].submit().follow()
    resp = resp.forms['save-custom-view'].submit().follow()
    assert resp.text.count('<tr') == 0 + 1
    assert resp.text.count('"cell-user">userA<') == 0
    assert resp.text.count('"cell-user">userB<') == 0
    assert resp.text.count('"cell-user">admin<') == 0


def test_backoffice_custom_view_status_filter(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.data = {}
        if i == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'
        formdata.jump_status('new')
        formdata.store()

    # change status of latest
    formdata.jump_status('rejected')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('tbody tr').length == 2

    # filters
    resp.forms['listing-settings']['filter'].value = 'rejected'
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.pyquery('tbody tr').length == 1

    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.pyquery('tbody tr').length == 1

    resp.forms['listing-settings']['filter-operator'] = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 2
    assert resp.forms['save-custom-view']['update'].checked is True
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.select()[0].filters == {
        'filter-operator': 'ne',
        'filter': 'rejected',
        'filter-status': 'on',
    }
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.forms['listing-settings']['filter-operator'].value == 'ne'
    assert resp.pyquery('tbody tr').length == 2


def test_backoffice_custom_view_delete(pub):
    user = create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/custom-test-view/')

    resp = resp.click('Delete')
    resp = resp.form.submit()
    assert resp.location.endswith('/management/form-title/')
    resp = resp.follow()
    assert 'custom test view' not in resp.text

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {
        'default': 'plop',
        'custom-view:custom-test-view': 'FOO {{ form_var_foo }}',
        'custom-view:another-view': '{{ form_var_foo }}',
    }
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    resp = app.get('/backoffice/data/foo/custom-test-view/')
    resp = resp.click('Delete')
    resp = resp.form.submit()
    assert resp.location.endswith('/data/foo/')
    resp = resp.follow()
    assert 'custom test view' not in resp.text

    carddef.refresh_from_storage()
    assert carddef.digest_templates == {
        'default': 'plop',
        'custom-view:another-view': '{{ form_var_foo }}',
    }


def test_backoffice_custom_map_view(pub):
    user = create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.geolocations = {'base': 'Geolocafoobar'}
    formdef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {'filter-1': True, 'filter-1-value': 'baz'}
    custom_view.user = user
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/custom-test-view/')
    resp = resp.click('Plot on a Map')
    assert resp.forms['listing-settings']['filter-1-value'].value == 'baz'


def test_backoffice_custom_view_reserved_slug(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')

    resp.forms['listing-settings']['user-label'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'user custom test view'
    resp = resp.forms['save-custom-view'].submit()
    # check slug not created with "user" as prefix
    assert resp.location.endswith('/user-userx-custom-test-view/')
    resp = resp.follow()

    for view_slug in ('Export', 'Geojson', 'ics'):
        # check slug not created with view name
        resp = app.get('/backoffice/management/form-title/')
        resp.forms['listing-settings']['user-label'].checked = False
        resp = resp.forms['listing-settings'].submit().follow()
        resp.forms['save-custom-view']['title'] = view_slug
        resp.forms['save-custom-view']['visibility'] = 'any'
        resp = resp.forms['save-custom-view'].submit()
        assert resp.location.endswith('/x-%s/' % view_slug.lower())
        resp = resp.follow()


def test_backoffice_custom_view_visibility(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    agent = pub.user_class(name='agent')
    agent.roles = [formdef.workflow_roles['_receiver']]
    agent.store()

    account = PasswordAccount(id='agent')
    account.set_password('agent')
    account.user_id = agent.id
    account.store()

    app = login(get_app(pub), username='agent', password='agent')
    resp = app.get('/backoffice/management/form-title/')

    # columns
    resp.forms['listing-settings']['user-label'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.text.count('<span>User Label</span>') == 0

    resp.forms['save-custom-view']['title'] = 'custom test view'
    assert resp.forms['save-custom-view'].fields['visibility'][0].options == [
        ('owner', True, None),
        ('role', False, None),
    ]
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view/')
    resp = resp.follow()
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.forms['save-custom-view']['update'].checked is True

    # second agent
    agent2 = pub.user_class(name='agent2')
    agent2.roles = [formdef.workflow_roles['_receiver']]
    agent2.store()

    account = PasswordAccount(id='agent2')
    account.set_password('agent2')
    account.user_id = agent2.id
    account.store()

    app = login(get_app(pub), username='agent2', password='agent2')
    resp = app.get('/backoffice/management/form-title/')
    assert 'custom test view' not in resp

    # shared custom view
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'shared view'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()

    app = login(get_app(pub), username='agent2', password='agent2')
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('shared view')

    # don't allow a second "any" view with same slug
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'shared view'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()
    assert {(x.slug, x.visibility) for x in get_publisher().custom_view_class.select()} == {
        ('custom-test-view', 'owner'),
        ('shared-view', 'any'),
        ('shared-view-2', 'any'),
    }


def test_backoffice_custom_view_any_visibility(pub):
    pub.user_class.wipe()
    pub.role_class.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    role1 = pub.role_class(name='foo')
    role1.allows_backoffice_access = True
    role1.store()

    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='field', display_locations=['listings'])]
    formdef.workflow_roles = {'_receiver': role1.id}
    formdef.store()

    agent = pub.user_class(name='agent')
    agent.roles = [role1.id]
    agent.store()

    account = PasswordAccount(id='agent')
    account.set_password('agent')
    account.user_id = agent.id
    account.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'Test view for all'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/test-view-for-all/')
    assert pub.custom_view_class.count() == 1

    app = login(get_app(pub), username='agent', password='agent')
    resp = app.get('/backoffice/management/form-title/test-view-for-all/')
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['save-custom-view']['qs'] = 'id=on&1=on'  # set from js
    assert resp.forms['save-custom-view']['title'].value == 'Test view for all'
    assert [x[0] for x in resp.forms['save-custom-view']['visibility'].options] == ['owner', 'role']
    resp.forms['save-custom-view']['visibility'] = 'owner'
    assert 'update' not in resp.forms['save-custom-view'].fields  # do not allow updating view
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.count() == 2


def test_backoffice_custom_view_role_visibility(pub):
    pub.user_class.wipe()
    pub.role_class.wipe()
    role1 = pub.role_class(name='foo')
    role1.allows_backoffice_access = True
    role1.store()
    role2 = pub.role_class(name='bar')
    role2.allows_backoffice_access = True
    role2.store()
    role3 = pub.role_class(name='baz')
    role3.allows_backoffice_access = True
    role3.store()

    FormDef.wipe()
    pub.custom_view_class.wipe()

    Workflow.wipe()
    workflow = Workflow(name='wf')
    workflow.roles = {'_foobar': 'Foobar', '_baz': 'Baz'}

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_foobar': role1.id, '_baz': role3.id}
    formdef.store()

    agent = pub.user_class(name='agent')
    agent.roles = [role1.id, role2.id]
    agent.store()

    account = PasswordAccount(id='agent')
    account.set_password('agent')
    account.user_id = agent.id
    account.store()

    app = login(get_app(pub), username='agent', password='agent')
    resp = app.get('/backoffice/management/form-title/')

    # columns
    resp.forms['listing-settings']['user-label'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<span>User Label</span>') == 0

    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp.forms['save-custom-view']['visibility'] = 'role'
    # only user roles listed in formdef functions are displayed
    # (role2 is not there as it's not in the formdef functions and
    # role3 is not there as the user is not a member)
    assert resp.forms['save-custom-view']['role'].options == [('None', True, '---'), (role1.id, False, 'foo')]
    resp.forms['save-custom-view']['role'] = role1.id
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/custom-test-view/')
    resp = resp.follow()
    assert resp.text.count('<span>User Label</span>') == 0

    # second agent
    agent2 = pub.user_class(name='agent2')
    agent2.roles = [role3.id]
    agent2.store()

    account = PasswordAccount(id='agent2')
    account.set_password('agent2')
    account.user_id = agent2.id
    account.store()

    app = login(get_app(pub), username='agent2', password='agent2')
    resp = app.get('/backoffice/management/form-title/')
    assert 'custom test view' not in resp
    resp = app.get('/backoffice/management/form-title/custom-test-view/', status=302).follow()
    assert 'A custom view for which you do not have access rights' in resp

    agent2.roles = [role1.id, role3.id]
    agent2.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'custom test view' in resp.text
    resp = app.get('/backoffice/management/form-title/custom-test-view/', status=200)
    assert resp.forms['listing-settings']['user-label'].checked is False

    # allow updating view
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['listing-settings']['last_update_time'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp.forms['save-custom-view']['visibility'] = 'role'
    resp.forms['save-custom-view']['role'] = role1.id
    resp.forms['save-custom-view']['update'].checked = True
    resp = resp.forms['save-custom-view'].submit()
    assert {(x.slug, x.visibility) for x in get_publisher().custom_view_class.select()} == {
        ('custom-test-view', 'role')
    }

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('custom test view')
    assert resp.forms['listing-settings']['user-label'].checked is True
    assert resp.forms['listing-settings']['last_update_time'].checked is False

    # do not allow duplicated slugs
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['listing-settings']['last_update_time'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view'
    resp.forms['save-custom-view']['visibility'] = 'role'
    resp.forms['save-custom-view']['role'] = role1.id
    resp.forms['save-custom-view']['update'].checked = False
    resp = resp.forms['save-custom-view'].submit()
    assert {(x.slug, x.visibility) for x in get_publisher().custom_view_class.select()} == {
        ('custom-test-view', 'role'),
        ('custom-test-view-2', 'role'),
    }

    # check role view is accessible after the role is no longer statically assigned
    # to a form function.
    formdef.workflow_roles = {}
    formdef.store()
    # create a formdata dispatched to agent to give access
    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_foobar': str(agent2.roles[0])}
    formdata.jump_status('new')
    formdata.store()
    resp = app.get('/backoffice/management/form-title/custom-test-view/', status=200)
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['listing-settings']['last_update_time'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    # only title can be changed
    assert 'role' not in resp.forms['save-custom-view'].fields
    resp.forms['save-custom-view']['title'] = 'custom test view change'
    resp = resp.forms['save-custom-view'].submit()
    resp = resp.follow()
    assert resp.pyquery('#appbar h2').text() == 'form title - custom test view change'

    # check agent cannot change a "any" view
    formdef.workflow_roles = {'_foobar': role1.id, '_baz': role3.id}
    formdef.store()
    assert pub.custom_view_class.count() == 2
    view = pub.custom_view_class.get_by_slug('custom-test-view')
    view.user_id = ''
    view.visibility = 'any'
    view.store()
    resp = app.get('/backoffice/management/form-title/custom-test-view/', status=200)
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['listing-settings']['last_update_time'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    assert not resp.forms['save-custom-view']['visibility'].value  # not set, "any" is not available
    assert resp.forms['save-custom-view']['visibility'].options == [
        ('owner', False, None),
        ('role', False, None),
    ]
    resp = resp.forms['save-custom-view'].submit()
    resp = resp.follow()
    assert resp.pyquery('.messages').text() == 'Visibility must be set.'
    # set visibility
    resp.forms['listing-settings']['user-label'].checked = True
    resp.forms['listing-settings']['last_update_time'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp = resp.forms['save-custom-view'].submit()
    resp = resp.follow()
    assert pub.custom_view_class.count() == 3


def test_backoffice_carddef_custom_view_visibility(pub):
    user = create_superuser(pub)

    CardDef.wipe()
    pub.custom_view_class.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()

    app = login(get_app(pub))

    # shared custom view
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'foo'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()

    # don't allow a second "any" view with same slug
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'foo'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()
    assert {(x.slug, x.visibility) for x in get_publisher().custom_view_class.select()} == {
        ('foo', 'any'),
        ('foo-2', 'any'),
    }

    # and don't allow a "datasource" view with same slug
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'foo'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit()
    assert {(x.slug, x.visibility) for x in get_publisher().custom_view_class.select()} == {
        ('foo', 'any'),
        ('foo-2', 'any'),
        ('foo-3', 'datasource'),
    }


def test_backoffice_custom_view_is_default(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe(restart_sequence=True)
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    # private custom view (agent)
    agent = pub.user_class(name='agent')
    agent.roles = [formdef.workflow_roles['_receiver']]
    agent.store()
    account = PasswordAccount(id='agent')
    account.set_password('agent')
    account.user_id = agent.id
    account.store()
    app = login(get_app(pub), username='agent', password='agent')
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 1'
    resp = resp.forms['save-custom-view'].submit()

    # other private custom view (admin)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 2'
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()

    # shared custom view
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 3'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()

    assert pub.custom_view_class.count() == 3
    assert pub.custom_view_class.get(1).is_default is False  # simple user - private
    assert pub.custom_view_class.get(2).is_default is True  # super user - private
    assert pub.custom_view_class.get(3).is_default is True  # super user - shared

    # role default view
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 4'
    resp.forms['save-custom-view']['visibility'] = 'role'
    resp.forms['save-custom-view']['role'] = agent.roles[0]
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()

    assert pub.custom_view_class.count() == 4
    assert pub.custom_view_class.get(1).is_default is False  # simple user - private
    assert pub.custom_view_class.get(2).is_default is True  # super user - private
    assert pub.custom_view_class.get(3).is_default is True  # super user - shared
    assert pub.custom_view_class.get(4).is_default is True  # role view

    # not possible to define more than one default private view
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 5'
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.count() == 5
    assert pub.custom_view_class.get(1).is_default is False  # simple user - private
    assert pub.custom_view_class.get(2).is_default is False  # super user - private
    assert pub.custom_view_class.get(3).is_default is True  # super user - shared
    assert pub.custom_view_class.get(4).is_default is True  # role view
    assert pub.custom_view_class.get(5).is_default is True  # super user - private 2

    # not possible to define more than one default shared view
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 6'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.count() == 6
    assert pub.custom_view_class.get(1).is_default is False  # simple user - private
    assert pub.custom_view_class.get(2).is_default is False  # super user - private
    assert pub.custom_view_class.get(3).is_default is False  # super user - shared
    assert pub.custom_view_class.get(4).is_default is True  # role view
    assert pub.custom_view_class.get(5).is_default is True  # super user - private 2
    assert pub.custom_view_class.get(6).is_default is True  # super user - shared 2

    # check simple agent can also define one of its views as default
    app = login(get_app(pub), username='agent', password='agent')
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('.sidebar-custom-views .default-custom-view').parent().text() == 'view 4 (default)'
    resp = app.get('/backoffice/management/form-title/user-view-1/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.get(1).is_default is True
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('.sidebar-custom-views .default-custom-view').parent().text() == 'view 1 (default)'

    # check other default views are still default views
    assert pub.custom_view_class.get(4).is_default is True  # role view
    assert pub.custom_view_class.get(5).is_default is True  # super user - private 2
    assert pub.custom_view_class.get(6).is_default is True  # super user - shared 2

    # check most specific default custom view is used
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('#appbar h2').text() == 'form title - view 5'  # owner

    pub.custom_view_class.remove_object(5)
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('#appbar h2').text() == 'form title - view 4'  # role

    pub.custom_view_class.remove_object(4)
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('#appbar h2').text() == 'form title - view 6'  # any


def test_backoffice_default_custom_view(pub):
    user = create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field 1',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(4):
        formdata = formdef.data_class()()
        formdata.data = {}
        if i == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'
        if i < 3:
            formdata.jump_status('new')
        else:
            formdata.status = 'draft'
        formdata.store()

    app = login(get_app(pub))
    # define a shared default view
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-1-value': 'foo',
        'filter-1-operator': 'ne',
        'filter': 'all',
        'filter-1': 'on',
        'filter-status': 'on',
    }
    custom_view.user = user
    custom_view.is_default = True
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<span>field 1</span>') == 0
    assert resp.text.count('<tr') == 3

    # check that default view is applied
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<span>field 1</span>') == 0
    assert resp.text.count('<tr') == 3

    # define a user default view
    custom_view = pub.custom_view_class()
    custom_view.title = 'private custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'owner'
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {'filter-1-value': 'foo', 'filter': 'all', 'filter-1': 'on', 'filter-status': 'on'}
    custom_view.user = user
    custom_view.is_default = True
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/user-private-custom-test-view/')
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<span>field 1</span>') == 1
    assert resp.text.count('<tr') == 2

    # check that private default view is applied, and not shared default view
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<span>User Label</span>') == 0
    assert resp.text.count('<span>field 1</span>') == 1
    assert resp.text.count('<tr') == 2

    # check it's also applied in exports
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert resp.text.splitlines() == ['field 1', 'foo']


@pytest.mark.parametrize('klass', [FormDef, CardDef])
def test_backoffice_missing_custom_view(pub, klass):
    create_superuser(pub)

    klass.wipe()
    pub.custom_view_class.wipe()
    formdef = klass()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_url(backoffice=True) + 'user-plop/')
    assert resp.location == formdef.get_url(backoffice=True)
    resp = resp.follow()
    assert 'A missing or invalid custom view was referenced' in resp

    resp = app.get(formdef.get_url(backoffice=True) + 'user-plop/1/')
    assert resp.location == formdef.get_url(backoffice=True) + '1/'

    resp = app.get(formdef.get_url(backoffice=True) + 'user-plop/1/?plop')
    assert resp.location == formdef.get_url(backoffice=True) + '1/?plop'


@pytest.mark.parametrize('klass', [FormDef, CardDef])
def test_backoffice_unauthorized_role_custom_view(pub, klass):
    create_superuser(pub)
    role = pub.role_class(name='test')
    role.store()

    klass.wipe()
    pub.custom_view_class.wipe()
    formdef = klass()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    app.get(formdef.get_url(backoffice=True) + 'plop/', status=404)

    custom_view = get_publisher().custom_view_class()
    custom_view.title = 'plop'
    custom_view.formdef = formdef
    custom_view.visibility = 'role'
    custom_view.role_id = role.id
    custom_view.store()

    resp = app.get(formdef.get_url(backoffice=True) + 'plop/')
    assert resp.location == formdef.get_url(backoffice=True)
    resp = resp.follow()
    assert 'A custom view for which you do not have access rights' in resp

    resp = app.get(formdef.get_url(backoffice=True) + 'plop/?xxx')
    assert resp.location == formdef.get_url(backoffice=True) + '?xxx'

    resp = app.get(formdef.get_url(backoffice=True) + 'plop/1/')
    assert resp.location == formdef.get_url(backoffice=True) + '1/'

    resp = app.get(formdef.get_url(backoffice=True) + 'plop/1/?xxx')
    assert resp.location == formdef.get_url(backoffice=True) + '1/?xxx'


def test_backoffice_custom_view_columns(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field 1',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    assert resp.pyquery('th').length == 2
    assert resp.pyquery('th').text().strip() == 'Number'

    custom_view.columns = {'list': [{'id': 'unknown'}]}
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    # columns not found
    assert resp.pyquery('th').length == 1
    assert not resp.pyquery('th').text()


def test_backoffice_custom_view_sort_field(pub):
    create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field 1',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='2',
            label='field 2',
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo', '1_display': 'foo', '2': 'foo foo'}
    formdata.jump_status('new')
    formdata.store()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'bar', '1_display': 'bar', '2': 'foo foo'}
    formdata.jump_status('new')
    formdata.store()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'baz', '1_display': 'baz', '2': 'foo'}
    formdata.jump_status('new')
    formdata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.order_by = 'f1'
    custom_view.is_default = True
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    assert resp.text.count('<tr') == 4
    # bar, baz, foo
    assert re.findall(r'<a href="(\d)/">1-(\d)</a>', resp.text) == [('2', '2'), ('3', '3'), ('1', '1')]

    custom_view.order_by = '-f1'
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    assert resp.text.count('<tr') == 4
    # foo, baz, bar
    assert re.findall(r'<a href="(\d)/">1-(\d)</a>', resp.text) == [('1', '1'), ('3', '3'), ('2', '2')]

    custom_view.order_by = 'unknown'
    custom_view.store()
    # unknown sort field, ignore it
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    assert resp.text.count('<tr') == 4

    # check rank takes over when searching on text
    custom_view.order_by = '-f1'
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/shared-custom-test-view/')
    resp.forms['listing-settings']['q'] = 'foo'
    resp = resp.forms['listing-settings'].submit().follow()
    assert re.findall(r'<a href="(\d)/">1-(\d)</a>', resp.text) == [('1', '1'), ('2', '2'), ('3', '3')]

    # but can still be overridden by query string
    resp.forms['listing-settings']['order_by'] = '-f1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert re.findall(r'<a href="(\d)/">1-(\d)</a>', resp.text) == [('1', '1'), ('3', '3'), ('2', '2')]


def test_carddata_custom_view(pub):
    user = create_user(pub)

    CardDef.wipe()
    pub.custom_view_class.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'card view'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-card-view/')
    resp = resp.follow()


def test_carddata_custom_view_is_default(pub):
    user = create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    user.roles = [role.id]
    user.store()

    CardDef.wipe()
    pub.custom_view_class.wipe(restart_sequence=True)
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    # datasource custom view
    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view 3'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp.forms['save-custom-view']['is_default'] = True
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.get(1).is_default is False  # not for datasource


def test_carddata_custom_view_digest_template(pub):
    user = create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    user.roles = [role.id]
    user.store()

    CardDef.wipe()
    pub.custom_view_class.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['save-custom-view']['digest_template'].value == ''
    resp.forms['save-custom-view']['title'] = 'some view'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp.forms['save-custom-view']['digest_template'] = 'FOO {{ form_var_foo }} bar'
    resp = resp.forms['save-custom-view'].submit()
    assert CardDef.get(1).digest_templates == {'custom-view:some-view': 'FOO {{ form_var_foo }} bar'}
    assert resp.location.endswith('/some-view/')

    resp = resp.follow()
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['save-custom-view']['digest_template'].value == 'FOO {{ form_var_foo }} bar'
    assert resp.forms['save-custom-view']['update'].checked is True
    resp.forms['save-custom-view']['digest_template'] = 'FOO {{ form_var_foo }}'
    resp = resp.forms['save-custom-view'].submit()
    assert CardDef.get(1).digest_templates == {'custom-view:some-view': 'FOO {{ form_var_foo }}'}

    carddef = CardDef.get(1)
    carddef.digest_templates['default'] = 'plop'
    carddef.store()
    resp = app.get('/backoffice/data/foo/')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['save-custom-view']['digest_template'].value == 'plop'
    resp.forms['save-custom-view']['title'] = 'another view'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp.forms['save-custom-view']['digest_template'] = '{{ form_var_foo }}'
    resp = resp.forms['save-custom-view'].submit()
    assert CardDef.get(1).digest_templates == {
        'default': 'plop',
        'custom-view:some-view': 'FOO {{ form_var_foo }}',
        'custom-view:another-view': '{{ form_var_foo }}',
    }

    # change visibility to "owner", check there's no new digest_template added and
    # the previous one is removed.
    resp = app.get('/backoffice/data/foo/another-view/')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['save-custom-view']['digest_template'].value == '{{ form_var_foo }}'
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp.forms['save-custom-view'].submit()
    assert CardDef.get(1).digest_templates == {
        'default': 'plop',
        'custom-view:some-view': 'FOO {{ form_var_foo }}',
    }


def test_backoffice_custom_view_keep_filters(pub):
    user = create_superuser(pub)

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }
    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.ItemField(id='2', label='2nd field', data_source=datasource, varname='foo'),
    ]
    formdef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'owner'
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {'filter-1': True, 'filter-1-value': 'baz', 'filter-1-operator': 'lte'}
    custom_view.user = user
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/')
    assert 'filter-1-value' in resp.forms['listing-settings'].fields
    assert 'filter-1-operator' in resp.forms['listing-settings'].fields
    assert 'filter-2-value' not in resp.forms['listing-settings'].fields
    assert 'filter-2-operator' not in resp.forms['listing-settings'].fields

    resp = app.get('/backoffice/management/form-title/user-custom-test-view/?filter-foo=A')
    assert 'filter-1-value' not in resp.forms['listing-settings'].fields
    assert 'filter-1-operator' not in resp.forms['listing-settings'].fields
    assert 'filter-2-value' in resp.forms['listing-settings'].fields
    assert 'filter-2-operator' in resp.forms['listing-settings'].fields

    resp = app.get(
        '/backoffice/management/form-title/user-custom-test-view/?filter-foo=A&keep-view-filters=on'
    )
    assert 'filter-1-value' in resp.forms['listing-settings'].fields
    assert 'filter-1-operator' in resp.forms['listing-settings'].fields
    assert 'filter-2-value' in resp.forms['listing-settings'].fields
    assert 'filter-2-operator' in resp.forms['listing-settings'].fields


def test_backoffice_custom_view_boolean_filters(pub):
    user = create_superuser(pub)

    FormDef.wipe()
    pub.custom_view_class.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BoolField(id='1', label='1st field', display_locations=['validation', 'summary', 'listings']),
    ]
    formdef.store()
    formdef.data_class().wipe()
    for value in [True] * 5 + [False] * 2:
        formdata = formdef.data_class()()
        formdata.data = {'1': value}
        formdata.jump_status('new')
        formdata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'owner'
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {'filter-1': True, 'filter-1-value': 'true', 'filter': 'all'}
    custom_view.user = user
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/')
    assert resp.forms['listing-settings']['filter-1-value'].value == 'true'
    assert resp.forms['listing-settings']['filter-1-operator'].value == 'eq'
    assert resp.text.count('data-link=') == 5

    custom_view.filters = {'filter-1': True, 'filter-1-value': 'false', 'filter': 'all'}
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/')
    assert resp.forms['listing-settings']['filter-1-value'].value == 'false'
    assert resp.forms['listing-settings']['filter-1-operator'].value == 'eq'
    assert resp.text.count('data-link=') == 2

    custom_view.filters = {
        'filter-1': True,
        'filter-1-value': 'false',
        'filter-1-operator': 'ne',
        'filter': 'all',
    }
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/user-custom-test-view/')
    assert resp.forms['listing-settings']['filter-1-value'].value == 'false'
    assert resp.forms['listing-settings']['filter-1-operator'].value == 'ne'
    assert resp.text.count('data-link=') == 5


def test_item_options_in_custom_view(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    pub.custom_view_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    subcarddef = CardDef()
    subcarddef.name = 'sub-card-title'
    subcarddef.digest_templates = {'default': '{{ form_var_foo }}'}
    subcarddef.fields = [
        fields.StringField(
            id='1',
            label='1st field',
            type='string',
        ),
    ]
    subcarddef.workflow_roles = {'_editor': role.id}
    subcarddef.store()
    data_class = subcarddef.data_class()
    data_class.wipe()

    subcards = []
    for i in range(0, 20):
        carddata = data_class()
        carddata.data = {
            '1': 'plop%s' % (i + 1),
        }
        carddata.just_created()
        carddata.store()
        subcards.append(carddata)

    data_source_16 = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in list('azertyuiopqsdfghjklmwxcvbn')[:16]]),
    }
    data_source_15 = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in list('azertyuiopqsdfghjklmwxcvbn')[:15]]),
    }

    carddef = CardDef()
    carddef.name = 'card-title'
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.fields = [
        fields.StringField(
            id='1',
            label='1st field',
            type='string',
        ),
        fields.ItemField(
            id='2',
            label='2nd field',
            type='item',
            data_source=data_source_16,
            display_locations=['validation', 'summary', 'listings'],
            display_mode='list',
        ),
        fields.ItemField(
            id='3',
            label='3rd field',
            type='item',
            data_source=data_source_15,
            display_locations=['validation', 'summary', 'listings'],
            display_mode='list',
        ),
        fields.ItemField(
            id='4',
            label='4th field',
            type='item',
            data_source=data_source_16,
            display_locations=['validation', 'summary', 'listings'],
            display_mode='autocomplete',
        ),
        fields.ItemField(
            id='5',
            label='5th field',
            type='item',
            data_source=data_source_15,
            display_locations=['validation', 'summary', 'listings'],
            display_mode='autocomplete',
        ),
        fields.ItemField(
            id='6',
            label='6th field',
            type='item',
            data_source={'type': 'carddef:%s' % subcarddef.slug},
            display_locations=['validation', 'summary', 'listings'],
            display_mode='list',
        ),
        fields.ItemField(
            id='7',
            label='7th field',
            type='item',
            items=list('azertyuiopqsdfghjklmwxcvbn')[:16],
            display_locations=['validation', 'summary', 'listings'],
            display_mode='list',
        ),
    ]
    carddef.workflow_roles = {'_editor': role.id}
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    used_subcards = set()
    for i in range(0, 12):
        carddata = data_class()
        card = random.choice(subcards)
        used_subcards.add(card)
        carddata.data = {
            '1': 'plop%s' % (i % 2),
            '2': 'a%s' % (i % 4),
            '2_display': 'a%s' % (i % 4),
            '3': 'a%s' % (i % 4),
            '3_display': 'a%s' % (i % 4),
            '4': 'a%s' % (i % 4),
            '4_display': 'a%s' % (i % 4),
            '5': 'a%s' % (i % 4),
            '5_display': 'a%s' % (i % 4),
            '6': str(card.id),
            '6_display': 'plop%s' % card.id,
        }
        carddata.just_created()
        carddata.store()

    datasource_custom_view = pub.custom_view_class()
    datasource_custom_view.title = 'custom test view for datasource'
    datasource_custom_view.formdef = carddef
    datasource_custom_view.visibility = 'datasource'
    datasource_custom_view.columns = {'list': [{'id': '1'}]}
    datasource_custom_view.filters = {}
    datasource_custom_view.store()

    any_custom_view = pub.custom_view_class()
    any_custom_view.title = 'custom test view for anyone'
    any_custom_view.formdef = carddef
    any_custom_view.visibility = 'any'
    any_custom_view.columns = {'list': [{'id': '1'}]}
    any_custom_view.filters = {}
    any_custom_view.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/data/card-title/custom-test-view-for-anyone/')
    # enable filters
    resp.forms['listing-settings']['filter-1'].checked = True
    resp.forms['listing-settings']['filter-2'].checked = True
    resp.forms['listing-settings']['filter-3'].checked = True
    resp.forms['listing-settings']['filter-4'].checked = True
    resp.forms['listing-settings']['filter-5'].checked = True
    resp.forms['listing-settings']['filter-6'].checked = True
    resp.forms['listing-settings']['filter-7'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    # field 2: select - all used options are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-2-value'].options] == [
        '',
        'a0',
        'a1',
        'a2',
        'a3',
    ]

    # field 3: select - all used options are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-3-value'].options] == [
        '',
        'a0',
        'a1',
        'a2',
        'a3',
    ]

    # field 4: select2 - all used options are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=4&_search=')
    assert [x['id'] for x in resp2.json['data']] == ['a0', 'a1', 'a2', 'a3']

    # field 5: select2 - all used options are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=5&_search=')
    assert [x['id'] for x in resp2.json['data']] == ['a0', 'a1', 'a2', 'a3']

    # field 6: select - all used options are listed
    assert (
        len([x[0] for x in resp.forms['listing-settings']['filter-6-value'].options])
        == len(used_subcards) + 1
    )
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=6&_search=')
    assert len([x['id'] for x in resp2.json['data']]) == len(used_subcards)

    # field 7: select - no datasource - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-7-value'].options] == [''] + list(
        'azertyuiopqsdfghjklmwxcvbn'
    )[:16]

    resp = app.get('/backoffice/data/card-title/custom-test-view-for-datasource/')
    # enable filters
    resp.forms['listing-settings']['filter-1'].checked = True
    resp.forms['listing-settings']['filter-2'].checked = True
    resp.forms['listing-settings']['filter-3'].checked = True
    resp.forms['listing-settings']['filter-4'].checked = True
    resp.forms['listing-settings']['filter-5'].checked = True
    resp.forms['listing-settings']['filter-6'].checked = True
    resp.forms['listing-settings']['filter-7'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    # field 2: select2 - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-2-value'].options] == ['', '{}']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=2&_search=')
    assert [x['id'] for x in resp2.json['data']] == list('azertyuiopqsdfghjklmwxcvbn')[:15] + ['{}']

    # field 3: select - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-3-value'].options] == [''] + list(
        'azertyuiopqsdfghjklmwxcvbn'
    )[:15] + ['{}']

    # field 4: select2 - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', '{}']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=4&_search=')
    assert [x['id'] for x in resp2.json['data']] == list('azertyuiopqsdfghjklmwxcvbn')[:15] + ['{}']

    # field 5: select - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == [''] + list(
        'azertyuiopqsdfghjklmwxcvbn'
    )[:15] + ['{}']

    # field 6: select2 - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-6-value'].options] == ['', '{}']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=6&_search=')
    assert len([x['id'] for x in resp2.json['data']]) == 16

    # field 7: select2 - all items are listed
    assert [x[0] for x in resp.forms['listing-settings']['filter-7-value'].options] == ['', '{}']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=7&_search=')
    assert [x['id'] for x in resp2.json['data']] == list('azertyuiopqsdfghjklmwxcvbn')[:15] + ['{}']

    datasource_custom_view.filters = {
        'filter-5': 'on',
        'filter-5-value': '{{ form_var_foo }}',
        'filter-6': 'on',
        'filter-6-value': '{{ form_var_bar }}',
    }
    datasource_custom_view.store()
    resp = app.get('/backoffice/data/card-title/custom-test-view-for-datasource/')
    assert resp.forms['listing-settings']['filter-5-value'].options == [
        ('', False, ''),
        ('a', False, 'a'),
        ('z', False, 'z'),
        ('e', False, 'e'),
        ('r', False, 'r'),
        ('t', False, 't'),
        ('y', False, 'y'),
        ('u', False, 'u'),
        ('i', False, 'i'),
        ('o', False, 'o'),
        ('p', False, 'p'),
        ('q', False, 'q'),
        ('s', False, 's'),
        ('d', False, 'd'),
        ('f', False, 'f'),
        ('g', False, 'g'),
        ('{}', False, 'custom value'),
        ('{{ form_var_foo }}', True, '{{ form_var_foo }}'),
    ]
    assert resp.forms['listing-settings']['filter-6-value'].options == [
        ('{{ form_var_bar }}', True, '{{ form_var_bar }}'),
        ('{}', False, 'custom value'),
    ]


@pytest.mark.parametrize('user_perms', ['admin', 'category_admin', 'category_not_admin', 'agent'])
def test_backoffice_hidden_data_source_custom_view(pub, user_perms):
    pub.user_class.wipe()

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store()

    if user_perms == 'admin':
        user = create_superuser(pub)
    elif user_perms == 'category_admin':
        user = create_user(pub)
        cat.management_roles = [pub.role_class.get(user.roles[0])]
        cat.store()
    else:
        user = create_user(pub)

    pub.custom_view_class.wipe()
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {
        'default': 'plop',
        'custom-view:custom-test-view': 'FOO {{ form_var_foo }}',
        'custom-view:datasource-view': '{{ form_var_foo }}',
    }
    if user_perms in ('category_admin', 'category_not_admin'):
        carddef.category = cat
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    visible_views = {x.attrib['href'] for x in resp.pyquery('.sidebar-custom-views a')}

    if user_perms in ('admin', 'category_admin'):
        assert visible_views == {'datasource-view/', 'custom-test-view/'}
    else:
        assert visible_views == {'custom-test-view/'}


def test_backoffice_custom_view_group_by(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    user = create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.ItemField(id='1', label='Category', items=['foo', 'bar', 'baz']),
        fields.StringField(id='2', label='Label', varname='foo', display_locations=['listings']),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {
        'default': '{{ form_var_foo }}',
        'custom-view:custom-test-view': '{{ form_var_foo }}',
    }
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': '2'}]}
    custom_view.filters = {}
    custom_view.order_by = 'f2'
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/custom-test-view/')
    resp.forms['save-custom-view']['group_by'] = '1'
    resp.forms['save-custom-view']['qs'] = 'id=on&1=on'  # set from js
    resp = resp.forms['save-custom-view'].submit()

    custom_view.refresh_from_storage()
    assert custom_view.group_by == '1'

    carddata_ids = []
    for group in ('bar', 'foo'):
        for i in range(3):
            carddata = carddef.data_class()()
            carddata.data = {'1': group, '2': string.ascii_letters[3 - i : 6 - i]}
            carddata.just_created()
            carddata.store()
            carddata_ids.append(str(carddata.id))

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field',
            varname='item',
            data_source={'type': f'carddef:{carddef.slug}:{custom_view.slug}'},
            display_locations=['validation', 'summary', 'listings'],
            display_mode='list',
        ),
        fields.ItemsField(
            id='2',
            label='field',
            data_source={'type': f'carddef:{carddef.slug}:{custom_view.slug}'},
            display_locations=['validation', 'summary', 'listings'],
            display_mode='checkboxes',
        ),
        fields.StringField(
            # field to get live url
            id='3',
            label='string',
            condition={'type': 'django', 'value': 'form_var_item'},
        ),
    ]
    formdef.store()
    resp = app.get(formdef.get_url())
    assert [x.attrib['label'] for x in resp.pyquery('#form_f1').find('optgroup')] == ['bar', 'foo']
    assert [x.text for x in resp.pyquery('#form_f1').find('option')] == [
        'bcd',
        'cde',
        'def',
        'bcd',
        'cde',
        'def',
    ]
    assert resp.pyquery('.CheckboxesWidget li').text() == 'bar bcd cde def foo bcd cde def'
    assert resp.pyquery('.CheckboxesWidget li input + span').text() == 'bcd cde def bcd cde def'

    formdef.fields[1].prefill = {'type': 'string', 'value': ','.join(carddata_ids[:3])}
    formdef.store()

    resp = app.get(formdef.get_url())
    for i, carddata_id in enumerate(carddata_ids):
        if i < 3:
            assert resp.form[f'f2$element{carddata_id}'].checked
        else:
            assert not resp.form[f'f2$element{carddata_id}'].checked
    live_url = resp.html.find('form').attrs['data-live-url']
    app.post(live_url, params=resp.form.submit_fields())


def test_backoffice_folded_data_sources(pub):
    pub.user_class.wipe()
    user = create_superuser(pub)

    pub.custom_view_class.wipe()
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {
        'default': 'plop',
        'custom-view:custom-test-view': 'FOO {{ form_var_foo }}',
        'custom-view:datasource-view': '{{ form_var_foo }}',
    }
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    assert resp.pyquery('.sidebar-custom-views').length == 2
    assert resp.pyquery('fieldset.foldable.folded .sidebar-custom-views').length == 1

    resp = resp.click('datasource view')
    assert resp.pyquery('.sidebar-custom-views').length == 2
    assert resp.pyquery('fieldset.foldable:not(.folded) .sidebar-custom-views').length == 1


@pytest.mark.parametrize('formdef_class', [FormDef, CardDef])
def test_backoffice_custom_view_and_snapshots(pub, formdef_class):
    user = create_superuser(pub)
    pub.custom_view_class.wipe()
    pub.snapshot_class.wipe()
    formdef_class.wipe()
    formdef = formdef_class()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.backoffice_submission_roles = user.roles
    formdef.workflow_roles = {'_editor': user.roles[0], '_receiver': 1}
    formdef.digest_templates = {
        'default': 'plop',
    }
    formdef.store()

    assert pub.snapshot_class.count() == 1
    app = login(get_app(pub))
    resp = app.get(formdef.get_backoffice_url())

    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view - owner'
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/user-custom-test-view-owner/')
    resp = resp.follow()
    custom_view = pub.custom_view_class.select()[-1]
    assert custom_view.visibility == 'owner'
    assert pub.custom_view_class.count() == 1
    assert pub.snapshot_class.count() == 1  # owner custom view, no store on formdef

    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert pub.custom_view_class.count() == 0
    assert pub.snapshot_class.count() == 1  # owner custom view, no store on formdef

    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view - role'
    resp.forms['save-custom-view']['role'] = user.roles[0]
    resp.forms['save-custom-view']['visibility'] = 'role'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/custom-test-view-role/')
    resp = resp.follow()
    custom_view = pub.custom_view_class.select()[-1]
    assert custom_view.visibility == 'role'
    assert pub.custom_view_class.count() == 1
    assert pub.snapshot_class.count() == 2  # role custom view, store formdef
    latest_snapshot = pub.snapshot_class.get_latest(formdef.xml_root_node, formdef.id)
    assert latest_snapshot.comment == 'New custom view (custom test view - role)'

    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert pub.custom_view_class.count() == 0
    assert pub.snapshot_class.count() == 3  # role custom view, store formdef
    latest_snapshot = pub.snapshot_class.get_latest(formdef.xml_root_node, formdef.id)
    assert latest_snapshot.comment == 'Deletion of custom view (custom test view - role)'

    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'custom test view - any'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()
    assert resp.location.endswith('/custom-test-view-any/')
    resp = resp.follow()
    custom_view = pub.custom_view_class.select()[-1]
    assert custom_view.visibility == 'any'
    assert pub.custom_view_class.count() == 1
    assert pub.snapshot_class.count() == 4  # any custom view, store formdef

    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert pub.custom_view_class.count() == 0
    assert pub.snapshot_class.count() == 5  # any custom view, store formdef

    if formdef_class == CardDef:
        resp.forms['listing-settings']['filter-1'].checked = True
        resp = resp.forms['listing-settings'].submit().follow()
        resp.forms['save-custom-view']['title'] = 'custom test view - datasource'
        resp.forms['save-custom-view']['visibility'] = 'datasource'
        resp = resp.forms['save-custom-view'].submit()
        assert resp.location.endswith('/custom-test-view-datasource/')
        resp = resp.follow()
        custom_view = pub.custom_view_class.select()[-1]
        assert custom_view.visibility == 'datasource'
        assert pub.custom_view_class.count() == 1
        assert pub.snapshot_class.count() == 6  # datasource custom view, store formdef

        resp = resp.click('Delete')
        resp = resp.form.submit().follow()
        assert pub.custom_view_class.count() == 0
        assert pub.snapshot_class.count() == 7  # datasource custom view, store formdef


def test_item_filter_card_custom_identifier(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()
    CardDef.wipe()
    FormDef.wipe()

    user = create_superuser(pub)
    role = pub.role_class(name='test')
    role.store()
    user.roles = [role.id]
    user.store()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='custom_id'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='string',
            data_source={'type': 'carddef:%s' % carddef.url_name},
            display_locations=['validation', 'summary', 'listings'],
        )
    ]
    formdef.store()
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    # create one formdata with attr1, and two with attr2
    for i in range(2):
        for _ in range(i + 1):
            formdata = formdef.data_class()()
            formdata.data = {'1': 'attr%s' % (i + 1)}
            formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
            formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
            formdata.just_created()
            formdata.store()
            formdata.perform_workflow()
            formdata.store()

    app = login(get_app(pub))

    resp = app.get(formdef.get_backoffice_url())
    assert len(resp.pyquery('tbody tr')) == 3

    # enable item filter
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert [x[0] for x in resp.forms['listing-settings']['filter-1-value'].options] == ['', 'attr1', 'attr2']
    resp.forms['listing-settings']['filter-1-value'] = 'attr2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert len(resp.pyquery('tbody tr')) == 2

    resp.forms['save-custom-view']['title'] = 'custom view'
    resp.forms['save-custom-view']['visibility'] = 'any'
    resp = resp.forms['save-custom-view'].submit()
    assert pub.custom_view_class.count() == 1
    custom_view = pub.custom_view_class.select()[0]
    assert custom_view.filters['filter-1-value'] == 'attr2'
