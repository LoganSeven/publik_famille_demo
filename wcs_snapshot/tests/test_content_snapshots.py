import copy
import datetime
import json
import os
import time

import pytest
from django.utils.timezone import localtime, make_aware
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
from wcs.tracking_code import TrackingCode
from wcs.wf.backoffice_fields import SetBackofficeFieldsWorkflowStatusItem
from wcs.wf.create_formdata import Mapping
from wcs.workflows import ContentSnapshotPart, EvolutionPart, Workflow, WorkflowBackofficeFieldsFormDef

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


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


@pytest.fixture
def role(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()
    return role


@pytest.fixture
def user(pub, role):
    pub.user_class.wipe()
    user = pub.user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.name_identifiers = ['0123456789']
    user.roles = [role.id]
    user.store()

    account = PasswordAccount(id='admin')
    account.set_password('admin')
    account.user_id = user.id
    account.store()

    return user


@pytest.fixture
def access(pub, role):
    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()
    return access


def test_formdata_create_and_edit_and_bo_field(pub, user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1', varname='plop'),
    ]
    st1 = workflow.add_status('Status1', 'st1')
    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [{'field_id': 'bo1', 'value': '{{ form_var_foo }}'}]
    setbo2 = st1.add_action('set-backoffice-fields')
    setbo2.fields = [{'field_id': 'bo1', 'value': '{{ "foo"|add:form_var_plop }}'}]
    jump = st1.add_action('jump')
    jump.status = 'st2'

    st2 = workflow.add_status('Status2', 'st2')

    editable = st2.add_action('editable', id='_editable')
    editable.by = ['_submitter']
    editable.status = st1.id
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/test/')
    resp.form['f1'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'The form has been recorded' in resp.text

    data_id = formdef.data_class().select()[0].id
    resp = app.get('/test/%s/' % data_id)
    assert 'button_editable-button' in resp.text

    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert resp.form['f1'].value == 'bar'
    resp.form['f1'].value = 'baz'
    resp = resp.form.submit('submit').follow()  # -> saved

    formdata = formdef.data_class().get(data_id)
    assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
    # creation, submit
    assert formdata.evolution[0].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[0].parts[0].formdef_id == formdef.id
    assert formdata.evolution[0].parts[0].old_data == {}
    assert formdata.evolution[0].parts[0].new_data == {'1': 'bar'}
    dt1 = formdata.evolution[0].parts[0].datetime
    # creation, bo field first action
    assert formdata.evolution[0].parts[1].formdef_type == 'formdef'
    assert formdata.evolution[0].parts[1].formdef_id == formdef.id
    assert formdata.evolution[0].parts[1].old_data == {'1': 'bar', 'bo1': None}
    assert formdata.evolution[0].parts[1].new_data == {'1': 'bar', 'bo1': 'bar'}
    dt2 = formdata.evolution[0].parts[1].datetime
    assert dt2 > dt1
    # creation, bo field second action
    assert formdata.evolution[0].parts[2].formdef_type == 'formdef'
    assert formdata.evolution[0].parts[2].formdef_id == formdef.id
    assert formdata.evolution[0].parts[2].old_data == {'1': 'bar', 'bo1': 'bar'}
    assert formdata.evolution[0].parts[2].new_data == {'1': 'bar', 'bo1': 'foobar'}
    dt3 = formdata.evolution[0].parts[2].datetime
    assert dt3 > dt2
    # update, submit
    assert formdata.evolution[1].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[1].parts[0].formdef_id == formdef.id
    assert formdata.evolution[1].parts[0].old_data == {'1': 'bar', 'bo1': 'foobar'}
    assert formdata.evolution[1].parts[0].new_data == {'1': 'baz', 'bo1': 'foobar'}
    dt4 = formdata.evolution[1].parts[0].datetime
    assert dt4 > dt3
    # update, bo field first action
    assert formdata.evolution[2].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[2].parts[0].formdef_id == formdef.id
    assert formdata.evolution[2].parts[0].old_data == {'1': 'baz', 'bo1': 'foobar'}
    assert formdata.evolution[2].parts[0].new_data == {'1': 'baz', 'bo1': 'baz'}
    dt5 = formdata.evolution[2].parts[0].datetime
    assert dt5 > dt4
    # update, bo field second action
    assert formdata.evolution[2].parts[1].formdef_type == 'formdef'
    assert formdata.evolution[2].parts[1].formdef_id == formdef.id
    assert formdata.evolution[2].parts[1].old_data == {'1': 'baz', 'bo1': 'baz'}
    assert formdata.evolution[2].parts[1].new_data == {'1': 'baz', 'bo1': 'foobaz'}
    dt6 = formdata.evolution[2].parts[1].datetime
    assert dt6 > dt5


def test_backoffice_formdata_submission(pub, user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='String'),
    ]
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
    assert formdata.evolution[0].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[0].parts[0].formdef_id == formdef.id
    assert formdata.evolution[0].parts[0].old_data == {}
    assert formdata.evolution[0].parts[0].new_data == {'1': 'test submission'}


def test_backoffice_carddata_add_edit(pub, user):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {
        '_viewer': user.roles[0],
        '_editor': user.roles[0],
    }
    carddef.store()
    carddef.data_class().wipe()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.roles = {
        '_viewer': 'Viewer',
        '_editor': 'Editor',
    }
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.status = 'st2'
    st2 = workflow.add_status('Status2', 'st2')
    editable = st2.add_action('editable', id='_editable')
    editable.by = ['_editor']
    editable.status = st1.id
    workflow.store()

    carddef.workflow_id = workflow.id
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/test/add/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit').follow()
    assert 'button_editable-button' in resp.text

    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    resp.form['f1'].value = 'bar'
    resp = resp.form.submit('submit').follow()

    carddata = carddef.data_class().select()[0]
    # creation
    assert isinstance(carddata.evolution[0].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'1': 'foo'}
    dt1 = carddata.evolution[0].parts[0].datetime
    # update
    assert isinstance(carddata.evolution[1].parts[0], ContentSnapshotPart)
    assert carddata.evolution[1].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[1].parts[0].formdef_id == carddef.id
    assert carddata.evolution[1].parts[0].old_data == {'1': 'foo'}
    assert carddata.evolution[1].parts[0].new_data == {'1': 'bar'}
    assert carddata.evolution[1].parts[0].user_id == user.id
    dt2 = carddata.evolution[1].parts[0].datetime
    assert dt2 > dt1


def test_backoffice_card_import_csv(pub, user):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test'),
        fields.StringField(id='2', label='Test2'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))

    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    data = [b'Test,Test2']
    data.append(b'data,foo')

    resp.forms[0]['file'] = Upload('test.csv', b'\n'.join(data), 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert 'Importing data into cards' in resp
    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert isinstance(carddata.evolution[-1].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'1': 'data', '2': 'foo'}


def test_backoffice_card_import_json(pub, user):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    data = {
        'data': [
            {
                'fields': {
                    'string': 'a string',
                }
            }
        ]
    }
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()

    carddata = carddef.data_class().select()[0]
    assert isinstance(carddata.evolution[-1].parts[0], ContentSnapshotPart)
    assert carddata.evolution[-1].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[-1].parts[0].formdef_id == carddef.id
    assert carddata.evolution[-1].parts[0].old_data == {}
    assert carddata.evolution[-1].parts[0].new_data == {'1': 'a string'}


@pytest.mark.parametrize('formdef_class', [FormDef, CardDef])
def test_backoffice_show_history(pub, user, formdef_class):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String'),
        fields.TextField(id='2', label='Text'),
        fields.EmailField(id='3', label='Email'),
        fields.BoolField(id='4', label='Bool'),
        fields.FileField(id='5', label='File'),
        fields.DateField(id='6', label='Date'),
        fields.ItemField(
            id='7',
            label='Item',
            data_source={
                'type': 'jsonvalue',
                'value': '[{"id": "a", "text": "a"}, {"id": "b", "text": "b"}]',
            },
        ),
        fields.ItemsField(
            id='8',
            label='Items',
            data_source={
                'type': 'jsonvalue',
                'value': '[{"id": "a", "text": "a"}, {"id": "b", "text": "b"}]',
            },
        ),
        fields.MapField(id='9', label='Map'),
        fields.PasswordField(id='10', label='Password'),
        fields.ComputedField(id='11', label='Computed'),
    ]
    block.store()

    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Backoffice field'),
    ]
    wf.add_status('Status1')
    wf.store()

    formdef_class.wipe()
    formdef = formdef_class()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='1', label='String'),
        fields.TextField(id='2', label='Text'),
        fields.EmailField(id='3', label='Email'),
        fields.BoolField(id='4', label='Bool'),
        fields.FileField(id='5', label='File'),
        fields.DateField(id='6', label='Date'),
        fields.ItemField(
            id='7',
            label='Item',
            data_source={
                'type': 'jsonvalue',
                'value': '[{"id": "a", "text": "a"}, {"id": "b", "text": "b"}]',
            },
        ),
        fields.ItemsField(
            id='8',
            label='Items',
            data_source={
                'type': 'jsonvalue',
                'value': '[{"id": "a", "text": "a"}, {"id": "b", "text": "b"}]',
            },
        ),
        fields.MapField(id='9', label='Map'),
        fields.PasswordField(id='10', label='Password'),
        fields.ComputedField(id='11', label='Computed'),
        fields.BlockField(id='12', label='Block', block_slug='foobar', max_items='3'),
    ]
    formdef.workflow_roles = {
        '_receiver': user.roles[0],
        '_editor': user.roles[0],
    }
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_id = wf.id
    formdef.store()

    upload1 = PicklableUpload('test.txt', 'text/plain')
    upload1.receive([b'base64me'])
    upload2 = PicklableUpload('test-bis.txt', 'text/plain')
    upload2.receive([b'rebase64me'])

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'blah',
        '2': 'foo bar blah',
        '3': 'foo@bar.com',
        '4': False,
        '5': upload1,
        '6': time.strptime('2022-11-04', '%Y-%m-%d'),
        '7': 'a',
        '7_display': 'a',
        '8': ['b'],
        '8_display': 'b',
        '9': {'lat': 1.5, 'lon': 2.25},
        '10': {'cleartext': 'foo'},
        '11': 'computed',
        '12': {
            'data': [
                {
                    '1': 'plop',
                    '2': 'plop plop',
                    '3': 'foo@baz.com',
                    '4': True,
                    '5': upload2,
                    '6': time.strptime('2022-11-05', '%Y-%m-%d'),
                    '7': 'b',
                    '7_display': 'b',
                    '8': ['a', 'b'],
                    '8_display': 'a, b',
                    '9': {'lat': 1.6, 'lon': 2.26},
                    '10': {'cleartext': 'bar'},
                },
            ],
            'schema': {'5': 'file'},
        },
        'bo1': 'foobar',
    }
    formdata.just_created()
    formdata.store()

    part1 = formdata.evolution[-1].parts[0]
    evo = formdata.evolution[-1]
    part2 = ContentSnapshotPart(formdata=formdata, old_data=copy.deepcopy(formdata.data))
    part2.new_data = {
        # '1' removed
        '2': 'foo bar blah',
        '3': 'foo@bar.com',
        '4': True,  # changed
        '5': upload1,
        '6': time.strptime('2022-11-04', '%Y-%m-%d'),
        '7': 'a',
        '7_display': 'a',
        '8': ['a', 'b'],  # changed
        '8_display': 'a, b',
        '9': {'lat': 1.5, 'lon': 2.25},
        '10': {'cleartext': 'fooo'},  # changed
        '11': 'computed',
        '12': {
            'data': [
                {
                    '1': 'plop',
                    '2': 'plop plop',
                    '3': 'foo@baz.com',
                    '4': True,
                    '5': upload1,  # changed
                    '6': time.strptime('2022-11-05', '%Y-%m-%d'),
                    '7': 'b',
                    '7_display': 'b',
                    '8': ['a', 'b'],
                    '8_display': 'a, b',
                    '9': {'lat': 1.6, 'lon': 2.27},  # changed
                    '10': {'cleartext': 'barr'},  # changed
                },
                {  # new element
                    '1': 'plop',
                    '2': 'plop plop',
                    '3': 'foo@baz.com',
                    '4': True,
                    '5': upload2,
                    '6': time.strptime('2022-11-05', '%Y-%m-%d'),
                    '7': 'b',
                    '7_display': 'b',
                    '8': ['a', 'b'],
                    '8_display': 'a, b',
                    '9': {'lat': 1.6, 'lon': 2.26},
                    '10': {'cleartext': 'bar'},
                },
            ],
            'schema': {'5': 'file'},
        },
        'bo1': 'foobar',
    }
    evo.add_part(part2)
    part3 = ContentSnapshotPart(formdata=formdata, old_data=copy.deepcopy(part2.new_data))
    part3.new_data = {
        '1': 'reset',  # added
        '2': 'foo bar blah',
        '3': 'foo@bar.com',
        '4': True,
        '5': upload2,  # changed
        '6': time.strptime('2022-11-06', '%Y-%m-%d'),  # changed
        '7': 'b',  # changed
        '7_display': 'b',
        '8': ['a', 'b'],
        '8_display': 'a, b',
        '9': {'lat': 1.5, 'lon': 2.26},  # changed
        '10': {'cleartext': 'fooo'},
        '11': 'computed',
        '12': {
            'data': [
                {
                    # '1' removed
                    '2': 'plop plop',
                    '3': 'foo@baz.com',
                    '4': False,  # changed
                    '5': upload1,
                    '6': time.strptime('2022-11-05', '%Y-%m-%d'),
                    '7': 'b',
                    '7_display': 'b',
                    '8': ['a', 'b'],
                    '8_display': 'a, b',
                    # '9' removed
                    '10': {'cleartext': 'barr'},
                },
                # second element removed
            ],
            'schema': {'5': 'file'},
        },
        'bo1': 'foobar',
    }
    evo.add_part(part3)
    part4 = ContentSnapshotPart(formdata=formdata, old_data=copy.deepcopy(part3.new_data))
    part4.new_data = {
        '1': 'reset',
        '2': 'foo bar blah',
        '3': 'foo@bar.com',
        '4': True,
        # '5' removed
        '6': time.strptime('2022-11-06', '%Y-%m-%d'),
        '7': 'b',
        '7_display': 'b',
        '8': ['a', 'b'],
        '8_display': 'a, b',
        '9': {'lat': 1.5, 'lon': 2.26},
        '10': {'cleartext': 'fooo'},
        '11': 'computed',
        '12': {
            'data': [
                {
                    '2': 'plop plop',
                    '3': 'foo@baz.com',
                    '4': False,
                    '5': upload1,
                    '6': time.strptime('2022-11-05', '%Y-%m-%d'),
                    '7': 'b',
                    '7_display': 'b',
                    '8': ['a', 'b'],
                    '8_display': 'a, b',
                    '10': {'cleartext': 'barr'},
                },
            ],
            'schema': {'5': 'file'},
        },
        'bo1': 'foobar',
    }
    evo.add_part(part4)
    formdata.store()
    part5 = ContentSnapshotPart(formdata=formdata, old_data=copy.deepcopy(part4.new_data))
    part5.new_data = {
        '1': 'reset',
        '2': 'foo bar blah',
        '3': 'foo@bar.com',
        '4': True,
        '6': time.strptime('2022-11-06', '%Y-%m-%d'),
        '7': 'b',
        '7_display': 'b',
        '8': ['a', 'b'],
        '8_display': 'a, b',
        '9': {'lat': 1.5, 'lon': 2.26},
        '10': {'cleartext': 'fooo'},
        '11': 'computed',
        # bad format, 12 is a block field
        '12': 'foobar',
        'bo1': 'foobar',
    }
    evo.add_part(part5)
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())

    assert (
        resp.pyquery('#evolutions fieldset[data-datetime="%s"] legend' % part1.datetime.isoformat()).text()
        == 'initial data'
    )
    table1 = '#evolutions table[data-datetime="%s"]' % part1.datetime.isoformat()
    assert resp.pyquery('%s tr[data-field-id="1"] td' % table1).text() == 'String — blah'
    assert resp.pyquery('%s tr[data-field-id="2"] td' % table1).text() == 'Text — foo bar blah'
    assert resp.pyquery('%s tr[data-field-id="3"] td' % table1).text() == 'Email — foo@bar.com'
    assert resp.pyquery('%s tr[data-field-id="4"] td' % table1).text() == 'Bool — False'
    assert resp.pyquery('%s tr[data-field-id="5"] td' % table1).text() == 'File — test.txt'
    assert resp.pyquery('%s tr[data-field-id="6"] td' % table1).text() == 'Date — 2022-11-04'
    assert resp.pyquery('%s tr[data-field-id="7"] td' % table1).text() == 'Item — a'
    assert resp.pyquery('%s tr[data-field-id="8"] td' % table1).text() == 'Items — b'
    assert resp.pyquery('%s tr[data-field-id="9"] td' % table1).text() == 'Map — new value'
    assert len(resp.pyquery('%s tr[data-field-id="10"]' % table1)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="11"]' % table1)) == 0
    assert resp.pyquery('%s tr[data-field-id="12"] td' % table1).text() == 'Block'
    assert len(resp.pyquery('%s tr[data-block-id="12"]' % table1)) == 1
    table1_el0 = '%s tr[data-element-num="0"]' % table1
    assert resp.pyquery('%s[data-block-id="12"] td' % table1_el0).text() == 'element number 1 (added)'
    assert resp.pyquery('%s[data-field-id="12_1"] td' % table1_el0).text() == 'String — plop'
    assert resp.pyquery('%s[data-field-id="12_2"] td' % table1_el0).text() == 'Text — plop plop'
    assert resp.pyquery('%s[data-field-id="12_3"] td' % table1_el0).text() == 'Email — foo@baz.com'
    assert resp.pyquery('%s[data-field-id="12_4"] td' % table1_el0).text() == 'Bool — True'
    assert resp.pyquery('%s[data-field-id="12_5"] td' % table1_el0).text() == 'File — test-bis.txt'
    assert resp.pyquery('%s[data-field-id="12_6"] td' % table1_el0).text() == 'Date — 2022-11-05'
    assert resp.pyquery('%s[data-field-id="12_7"] td' % table1_el0).text() == 'Item — b'
    assert resp.pyquery('%s[data-field-id="12_8"] td' % table1_el0).text() == 'Items — a, b'
    assert resp.pyquery('%s[data-field-id="12_9"] td' % table1_el0).text() == 'Map — new value'
    assert len(resp.pyquery('%s[data-field-id="12_10"]' % table1_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_11"]' % table1_el0)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="bo1"]' % table1)) == 0

    assert resp.pyquery(
        '#evolutions fieldset[data-datetime="%s"] legend' % part2.datetime.isoformat()
    ).text() == 'changed at %s' % localtime(part2.datetime).strftime('%Y-%m-%d %H:%M')
    table2 = '#evolutions table[data-datetime="%s"]' % part2.datetime.isoformat()
    assert resp.pyquery('%s tr[data-field-id="1"] td' % table2).text() == 'String blah —'
    assert len(resp.pyquery('%s tr[data-field-id="2"]' % table2)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="3"]' % table2)) == 0
    assert resp.pyquery('%s tr[data-field-id="4"] td' % table2).text() == 'Bool False True'
    assert len(resp.pyquery('%s tr[data-field-id="5"]' % table2)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="6"]' % table2)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="7"]' % table2)) == 0
    assert resp.pyquery('%s tr[data-field-id="8"] td' % table2).text() == 'Items b a, b'
    assert len(resp.pyquery('%s tr[data-field-id="9"]' % table2)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="10"]' % table2)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="11"]' % table2)) == 0
    assert resp.pyquery('%s tr[data-field-id="12"] td' % table2).text() == 'Block'
    assert len(resp.pyquery('%s tr[data-block-id="12"]' % table2)) == 2
    table2_el0 = '%s tr[data-element-num="0"]' % table2
    assert resp.pyquery('%s[data-block-id="12"] td' % table2_el0).text() == 'element number 1 (updated)'
    assert len(resp.pyquery('%s[data-field-id="12_1"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_2"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_3"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_4"]' % table2_el0)) == 0
    assert resp.pyquery('%s[data-field-id="12_5"] td' % table2_el0).text() == 'File test-bis.txt test.txt'
    assert len(resp.pyquery('%s[data-field-id="12_6"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_7"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_8"]' % table2_el0)) == 0
    assert resp.pyquery('%s[data-field-id="12_9"] td' % table2_el0).text() == 'Map old value new value'
    assert len(resp.pyquery('%s[data-field-id="12_10"]' % table2_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_11"]' % table2_el0)) == 0
    table2_el1 = '%s tr[data-element-num="1"]' % table2
    assert resp.pyquery('%s[data-block-id="12"] td' % table2_el1).text() == 'element number 2 (added)'
    assert resp.pyquery('%s[data-field-id="12_1"] td' % table2_el1).text() == 'String — plop'
    assert resp.pyquery('%s[data-field-id="12_2"] td' % table2_el1).text() == 'Text — plop plop'
    assert resp.pyquery('%s[data-field-id="12_3"] td' % table2_el1).text() == 'Email — foo@baz.com'
    assert resp.pyquery('%s[data-field-id="12_4"] td' % table2_el1).text() == 'Bool — True'
    assert resp.pyquery('%s[data-field-id="12_5"] td' % table2_el1).text() == 'File — test-bis.txt'
    assert resp.pyquery('%s[data-field-id="12_6"] td' % table2_el1).text() == 'Date — 2022-11-05'
    assert resp.pyquery('%s[data-field-id="12_7"] td' % table2_el1).text() == 'Item — b'
    assert resp.pyquery('%s[data-field-id="12_8"] td' % table2_el1).text() == 'Items — a, b'
    assert resp.pyquery('%s[data-field-id="12_9"] td' % table2_el1).text() == 'Map — new value'
    assert len(resp.pyquery('%s[data-field-id="12_10"]' % table2_el1)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_11"]' % table2_el1)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="bo1"]' % table2)) == 0

    assert resp.pyquery(
        '#evolutions fieldset[data-datetime="%s"] legend' % part3.datetime.isoformat()
    ).text() == 'changed at %s' % localtime(part3.datetime).strftime('%Y-%m-%d %H:%M')
    table3 = '#evolutions table[data-datetime="%s"]' % part3.datetime.isoformat()
    assert resp.pyquery('%s tr[data-field-id="1"] td' % table3).text() == 'String — reset'
    assert len(resp.pyquery('%s tr[data-field-id="2"]' % table3)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="3"]' % table3)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="4"]' % table3)) == 0
    assert resp.pyquery('%s tr[data-field-id="5"] td' % table3).text() == 'File test.txt test-bis.txt'
    assert resp.pyquery('%s tr[data-field-id="6"] td' % table3).text() == 'Date 2022-11-04 2022-11-06'
    assert resp.pyquery('%s tr[data-field-id="7"] td' % table3).text() == 'Item a b'
    assert len(resp.pyquery('%s tr[data-field-id="8"]' % table3)) == 0
    assert resp.pyquery('%s tr[data-field-id="9"] td' % table3).text() == 'Map old value new value'
    assert len(resp.pyquery('%s tr[data-field-id="10"]' % table3)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="11"]' % table3)) == 0
    assert resp.pyquery('%s tr[data-field-id="12"] td' % table3).text() == 'Block'
    assert len(resp.pyquery('%s tr[data-block-id="12"]' % table3)) == 2
    table3_el0 = '%s tr[data-element-num="0"]' % table3
    assert resp.pyquery('%s[data-block-id="12"] td' % table3_el0).text() == 'element number 1 (updated)'
    assert resp.pyquery('%s[data-field-id="12_1"] td' % table3_el0).text() == 'String plop —'
    assert len(resp.pyquery('%s[data-field-id="12_2"]' % table3_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_3"]' % table3_el0)) == 0
    assert resp.pyquery('%s[data-field-id="12_4"] td' % table3_el0).text() == 'Bool True False'
    assert len(resp.pyquery('%s[data-field-id="12_5"]' % table3_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_6"]' % table3_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_7"]' % table3_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_8"]' % table3_el0)) == 0
    assert resp.pyquery('%s[data-field-id="12_9"] td' % table3_el0).text() == 'Map old value —'
    assert len(resp.pyquery('%s[data-field-id="12_10"]' % table3_el0)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_11"]' % table3_el0)) == 0
    table3_el1 = '%s tr[data-element-num="1"]' % table3
    assert resp.pyquery('%s[data-block-id="12"] td' % table3_el1).text() == 'element number 2 (removed)'
    assert resp.pyquery('%s[data-field-id="12_1"] td' % table3_el1).text() == 'String plop —'
    assert resp.pyquery('%s[data-field-id="12_2"] td' % table3_el1).text() == 'Text plop plop —'
    assert resp.pyquery('%s[data-field-id="12_3"] td' % table3_el1).text() == 'Email foo@baz.com —'
    assert resp.pyquery('%s[data-field-id="12_4"] td' % table3_el1).text() == 'Bool True —'
    assert resp.pyquery('%s[data-field-id="12_5"] td' % table3_el1).text() == 'File test-bis.txt —'
    assert resp.pyquery('%s[data-field-id="12_6"] td' % table3_el1).text() == 'Date 2022-11-05 —'
    assert resp.pyquery('%s[data-field-id="12_7"] td' % table3_el1).text() == 'Item b —'
    assert resp.pyquery('%s[data-field-id="12_8"] td' % table3_el1).text() == 'Items a, b —'
    assert resp.pyquery('%s[data-field-id="12_9"] td' % table3_el1).text() == 'Map old value —'
    assert len(resp.pyquery('%s[data-field-id="12_10"]' % table3_el1)) == 0
    assert len(resp.pyquery('%s[data-field-id="12_11"]' % table3_el1)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="bo1"]' % table3)) == 0

    assert resp.pyquery(
        '#evolutions fieldset[data-datetime="%s"] legend' % part4.datetime.isoformat()
    ).text() == 'changed at %s' % localtime(part4.datetime).strftime('%Y-%m-%d %H:%M')
    table4 = '#evolutions table[data-datetime="%s"]' % part4.datetime.isoformat()
    assert len(resp.pyquery('%s tr[data-field-id="1"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="2"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="3"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="4"]' % table4)) == 0
    assert resp.pyquery('%s tr[data-field-id="5"] td' % table4).text() == 'File test-bis.txt —'
    assert len(resp.pyquery('%s tr[data-field-id="6"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="7"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="8"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="9"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="10"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="11"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="12"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="bo1"]' % table4)) == 0

    assert resp.pyquery(
        '#evolutions fieldset[data-datetime="%s"] legend' % part5.datetime.isoformat()
    ).text() == 'changed at %s' % localtime(part5.datetime).strftime('%Y-%m-%d %H:%M')
    table4 = '#evolutions table[data-datetime="%s"]' % part5.datetime.isoformat()
    assert len(resp.pyquery('%s tr[data-field-id="1"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="2"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="3"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="4"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="5"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="6"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="7"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="8"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="9"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="10"]' % table4)) == 0
    assert len(resp.pyquery('%s tr[data-field-id="11"]' % table4)) == 0
    assert resp.pyquery('%s tr[data-field-id="12"] td' % table3).text() == 'Block'
    assert len(resp.pyquery('%s tr[data-block-id="12"]' % table3)) == 2
    assert len(resp.pyquery('%s tr[data-field-id="bo1"]' % table4)) == 0

    # check user display
    part5 = ContentSnapshotPart(formdata=formdata, old_data=copy.deepcopy(part4.new_data))
    part5.new_data = copy.deepcopy(part4.new_data)
    part5.new_data['2'] = 'change'
    part5.user_id = user.id
    evo.add_part(part5)
    formdata.store()

    resp = app.get(formdata.get_backoffice_url())
    assert (
        resp.pyquery('.evolution--content-diff:last-child .evolution--content-diff-user').text()
        == '(%s)' % user.get_display_name()
    )

    # check invalid user display
    part5.user_id = '9999'
    formdata.store()
    resp = app.get(formdata.get_backoffice_url())
    assert not resp.pyquery('.evolution--content-diff:last-child .evolution--content-diff-user')


def test_workflow_formdata_create(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.formdef_slug = target_formdef.url_name
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_foo }}'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo'),
    ]
    source_formdef.workflow_id = wf.id
    source_formdef.store()

    formdata = source_formdef.data_class()()
    formdata.data = {'0': 'foobar'}
    formdata.just_created()
    formdata.store()

    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 1
    created_formdata = target_formdef.data_class().select()[0]
    assert isinstance(created_formdata.evolution[0].parts[0], ContentSnapshotPart)
    assert created_formdata.evolution[0].parts[0].formdef_type == 'formdef'
    assert created_formdata.evolution[0].parts[0].formdef_id == target_formdef.id
    assert created_formdata.evolution[0].parts[0].old_data == {}
    assert created_formdata.evolution[0].parts[0].new_data == {'0': 'foobar'}


def test_workflow_carddata_create(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression='{{ form_var_string }}'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'foobar'}
    formdata.just_created()
    formdata.store()

    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert isinstance(carddata.evolution[-1].parts[0], ContentSnapshotPart)
    assert carddata.evolution[-1].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[-1].parts[0].formdef_id == carddef.id
    assert carddata.evolution[-1].parts[0].old_data == {}
    assert carddata.evolution[-1].parts[0].new_data == {'1': 'foobar'}


def test_workflow_carddata_edit(pub):
    FormDef.wipe()
    CardDef.wipe()

    # carddef
    carddef = CardDef()
    carddef.name = 'Model 1'
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='string'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {
        '1': 'foobar',
    }
    carddata.just_created()
    carddata.store()

    # formdef workflow that will update carddata
    wf = Workflow(name='update')
    st1 = wf.add_status('New', 'st1')
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = 'st2'
    st2 = wf.add_status('Update card', 'st2')
    edit = st2.add_action('edit_carddata', id='edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_var_card }}'
    edit.mappings = [
        Mapping(field_id='1', expression='{{ form_var_foo }}'),
    ]
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'Update'
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='card'),
        fields.StringField(id='2', label='string', varname='foo'),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': str(carddata.id),
        '2': 'foobaz',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    carddata.refresh_from_storage()
    # creation
    assert isinstance(carddata.evolution[0].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'1': 'foobar'}
    dt1 = carddata.evolution[0].parts[0].datetime
    # update
    assert isinstance(carddata.evolution[1].parts[0], ContentSnapshotPart)
    assert carddata.evolution[1].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[1].parts[0].formdef_id == carddef.id
    assert carddata.evolution[1].parts[0].old_data == {'1': 'foobar'}
    assert carddata.evolution[1].parts[0].new_data == {'1': 'foobaz'}
    dt2 = carddata.evolution[1].parts[0].datetime
    assert dt2 > dt1

    # no changes; no new evolution
    formdata.store()
    formdata.perform_workflow()
    carddata.refresh_from_storage()
    assert len(carddata.evolution) == 2

    # but last evolution has a comment, so add a new evolution
    carddata.evolution[-1].comment = 'foobar'
    carddata.store()
    carddata.refresh_from_storage()
    formdata.store()
    formdata.perform_workflow()
    carddata.refresh_from_storage()
    assert len(carddata.evolution) == 3

    # last evolution is not empty, but contains only a ContentSnapshotPart; no new evolution
    part = ContentSnapshotPart(formdata=formdata, old_data={})
    carddata.evolution[-1].add_part(part)
    carddata.store()
    formdata.store()
    formdata.perform_workflow()
    carddata.refresh_from_storage()
    assert len(carddata.evolution) == 3

    # last evolution is not empty, add a new evolution
    carddata.evolution[-1].add_part(EvolutionPart())
    carddata.store()
    formdata.store()
    formdata.perform_workflow()
    carddata.refresh_from_storage()
    assert len(carddata.evolution) == 4


def test_workflow_set_backoffice_field(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        fields.StringField(id='0', label='String', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'HELLO'}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_string }}'}]
    item.parent = st1

    item.perform(formdata)
    formdata.refresh_from_storage()
    # creation
    assert isinstance(formdata.evolution[-1].parts[0], ContentSnapshotPart)
    assert formdata.evolution[-1].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[-1].parts[0].formdef_id == formdef.id
    assert formdata.evolution[-1].parts[0].old_data == {}
    assert formdata.evolution[-1].parts[0].new_data == {
        '0': 'HELLO',
    }
    dt1 = formdata.evolution[-1].parts[0].datetime
    # bo action
    assert isinstance(formdata.evolution[-1].parts[1], ContentSnapshotPart)
    assert formdata.evolution[-1].parts[1].formdef_type == 'formdef'
    assert formdata.evolution[-1].parts[1].formdef_id == formdef.id
    assert formdata.evolution[-1].parts[1].old_data == {
        '0': 'HELLO',
    }
    assert formdata.evolution[-1].parts[1].new_data == {
        '0': 'HELLO',
        'bo1': 'HELLO',
    }
    dt2 = formdata.evolution[-1].parts[1].datetime
    assert dt2 > dt1


def test_content_no_changes(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
        fields.FileField(id='bo2', label='2nd backoffice field'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        fields.StringField(id='0', label='String'),
        fields.FileField(id='1', label='File'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    upload1 = PicklableUpload('test.txt', 'text/plain')
    upload1.receive([b'base64me'])
    formdata.data = {'0': 'HELLO', '1': upload1}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    assert len(list(formdata.iter_evolution_parts())) == 1

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.fields = [{'field_id': 'bo1', 'value': 'HELLO2'}]
    item.parent = st1
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 2

    # no change
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 2

    # add bo file
    item.fields = [{'field_id': 'bo2', 'value': '{{ "hello"|qrcode }}'}]
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 3

    # no change
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 3

    # change in filename
    item.fields = [{'field_id': 'bo2', 'value': '{{ "hello"|qrcode:"test.png" }}'}]
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 4

    # no change
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 4

    # change in file content
    item.fields = [{'field_id': 'bo2', 'value': '{{ "hello2"|qrcode:"test.png" }}'}]
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 5

    # no change
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert len(list(formdata.iter_evolution_parts())) == 5


def test_api_form_submit(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='foobar', varname='foobar'),
    ]
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    data_class = formdef.data_class()

    payload = {
        'data': {
            'foobar': 'xxx',
        }
    }
    resp = app.post_json(
        '/api/formdefs/test/submit',
        payload,
    )
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
    assert formdata.evolution[0].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[0].parts[0].formdef_id == formdef.id
    assert formdata.evolution[0].parts[0].old_data == {}
    assert formdata.evolution[0].parts[0].new_data == {'1': 'xxx'}


def test_api_formdata_edit(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'foo@localhost',
    }
    formdata.user_id = user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.store()

    wfedit = workflow.possible_status[1].add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    app.post_json(
        '/api/forms/test/%s/' % formdata.id,
        {'data': {'0': 'bar@localhost'}},
    )
    formdata.refresh_from_storage()
    # creation
    assert isinstance(formdata.evolution[-1].parts[0], ContentSnapshotPart)
    assert formdata.evolution[-1].parts[0].formdef_type == 'formdef'
    assert formdata.evolution[-1].parts[0].formdef_id == formdef.id
    assert formdata.evolution[-1].parts[0].old_data == {}
    assert formdata.evolution[-1].parts[0].new_data == {'0': 'foo@localhost'}
    dt1 = formdata.evolution[-1].parts[0].datetime
    # update
    assert isinstance(formdata.evolution[-1].parts[1], ContentSnapshotPart)
    assert formdata.evolution[-1].parts[1].formdef_type == 'formdef'
    assert formdata.evolution[-1].parts[1].formdef_id == formdef.id
    assert formdata.evolution[-1].parts[1].old_data == {'0': 'foo@localhost'}
    assert formdata.evolution[-1].parts[1].new_data == {'0': 'bar@localhost'}
    dt2 = formdata.evolution[-1].parts[1].datetime
    assert dt2 > dt1


def test_api_card_import_csv(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar', varname='foo'),
        fields.StringField(id='1', label='foobar2', varname='foo2'),
    ]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.backoffice_submission_roles = [role.id]
    carddef.digest_templates = {'default': 'bla {{ form_var_foo }} xxx'}
    carddef.store()

    carddef.data_class().wipe()

    resp = app.put(
        '/api/cards/test/import-csv',
        params=b'foobar;foobar2\nfirst entry;plop\n',
        headers={'content-type': 'text/csv'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert isinstance(carddata.evolution[0].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'0': 'first entry', '1': 'plop'}


def test_api_card_import_json(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar', varname='foo'),
    ]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.backoffice_submission_roles = [role.id]
    carddef.store()

    carddef.data_class().wipe()

    data = {
        'data': [
            {
                'fields': {
                    'foo': 'bar',
                }
            },
        ]
    }
    resp = app.put_json(
        '/api/cards/test/import-json',
        data,
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert isinstance(carddata.evolution[0].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'0': 'bar'}


def test_api_card_submit(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='foobar', varname='foobar'),
    ]
    carddef.backoffice_submission_roles = [role.id]
    carddef.store()
    data_class = carddef.data_class()

    payload = {
        'data': {
            'foobar': 'xxx',
        }
    }
    resp = app.post_json(
        '/api/cards/test/submit',
        payload,
    )
    assert resp.json['err'] == 0
    carddata = data_class.get(resp.json['data']['id'])
    assert isinstance(carddata.evolution[0].parts[0], ContentSnapshotPart)
    assert carddata.evolution[0].parts[0].formdef_type == 'carddef'
    assert carddata.evolution[0].parts[0].formdef_id == carddef.id
    assert carddata.evolution[0].parts[0].old_data == {}
    assert carddata.evolution[0].parts[0].new_data == {'1': 'xxx'}


def test_api_formdata_at(pub, user, access, role):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1', varname='plop'),
    ]
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'foo',
        'bo1': 'bar',
    }
    formdata.just_created()
    formdata.evolution[-1].parts[0].datetime = make_aware(datetime.datetime(2022, 1, 2, 3, 4))
    formdata.store()

    def get_evo_and_parts(formdata):
        for evo in formdata.evolution:
            for part in evo.parts or []:
                if isinstance(part, ContentSnapshotPart):
                    yield part.datetime.strftime('%Y-%m-%d %H:%M'), part.old_data, part.new_data

    assert list(get_evo_and_parts(formdata)) == [('2022-01-02 03:04', {}, {'0': 'foo', 'bo1': 'bar'})]
    resp = app.get('/api/forms/test/%s/' % formdata.id)
    assert resp.json['fields'] == {'foobar': 'foo'}
    assert resp.json['workflow']['fields'] == {'plop': 'bar'}
    resp = app.get('/api/forms/test/list/?full=on')
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'bar'}

    # wrong format
    resp = app.get('/api/forms/test/%s/?at=bad-format' % formdata.id, status=400)
    assert resp.json['err_desc'] == 'Invalid value "bad-format" for "at".'
    resp = app.get('/api/forms/test/list/?full=on&at=bad-format', status=400)
    assert resp.json['err_desc'] == 'Invalid value "bad-format" for "at".'

    # before formdata creation
    resp = app.get(
        '/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T00:00:00+01:00'}, status=400
    )
    assert resp.json['err_desc'] == 'No data found for this datetime.'
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T00:00:00+01:00'})
    assert len(resp.json) == 0
    resp = app.get(
        '/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T03:04:00+01:00'}, status=400
    )
    assert resp.json['err_desc'] == 'No data found for this datetime.'
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T03:04:00+01:00'})
    assert len(resp.json) == 0

    # no ContentSnapshotPart (legacy formdata)
    formdata.evolution[0].parts = []
    formdata.store()
    assert list(get_evo_and_parts(formdata)) == []

    # add evolutions with ContentSnapshotPart
    evo = formdata.evolution[0]
    part = ContentSnapshotPart(formdata=formdata, old_data={})
    part.new_data = {'0': 'bar', 'bo1': 'foo'}
    part.datetime = make_aware(datetime.datetime(2022, 1, 2, 3, 4))
    evo.add_part(part)

    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    part = ContentSnapshotPart(formdata=formdata, old_data={'0': 'bar', 'bo1': 'foo'})
    part.new_data = {'0': 'baz', 'bo1': 'foo'}
    part.datetime = make_aware(datetime.datetime(2022, 1, 2, 3, 5))
    evo.add_part(part)
    formdata.evolution.append(evo)

    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    part = ContentSnapshotPart(formdata=formdata, old_data={'0': 'baz', 'bo1': 'foo'})
    part.new_data = {'0': 'foooo', '1': 'unknown', 'bo1': 'foo'}
    part.datetime = make_aware(datetime.datetime(2022, 1, 4, 5, 6))
    evo.add_part(part)
    formdata.evolution.append(evo)

    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    part = ContentSnapshotPart(formdata=formdata, old_data={'0': 'foooo', '1': 'unknown', 'bo1': 'foo'})
    part.new_data = {'0': 'fooo', 'bo1': 'foo'}
    part.datetime = make_aware(datetime.datetime(2022, 1, 5, 6, 7))
    evo.add_part(part)
    part = ContentSnapshotPart(formdata=formdata, old_data={'0': 'fooo', 'bo1': 'foo'})
    part.new_data = {'0': 'foo', 'bo1': 'bar'}
    part.datetime = make_aware(datetime.datetime(2022, 1, 5, 6, 7))
    evo.add_part(part)
    formdata.evolution.append(evo)

    formdata._store_all_evolution = True
    formdata.store()
    assert list(get_evo_and_parts(formdata)) == [
        ('2022-01-02 03:04', {}, {'0': 'bar', 'bo1': 'foo'}),
        ('2022-01-02 03:05', {'0': 'bar', 'bo1': 'foo'}, {'0': 'baz', 'bo1': 'foo'}),
        ('2022-01-04 05:06', {'0': 'baz', 'bo1': 'foo'}, {'0': 'foooo', '1': 'unknown', 'bo1': 'foo'}),
        ('2022-01-05 06:07', {'0': 'foooo', '1': 'unknown', 'bo1': 'foo'}, {'0': 'fooo', 'bo1': 'foo'}),
        ('2022-01-05 06:07', {'0': 'fooo', 'bo1': 'foo'}, {'0': 'foo', 'bo1': 'bar'}),
    ]

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-03T00:00:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'baz'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-03T00:00:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'baz'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T03:05:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'bar'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T03:05:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'bar'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T03:06:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'baz'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T03:06:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'baz'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-04T00:00:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'baz'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-04T00:00:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'baz'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-05T00:00:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'foooo'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-05T00:00:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foooo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-05T06:07:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'foooo'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-05T06:07:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foooo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-05T06:08:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'foo'}
    assert resp.json['workflow']['fields'] == {'plop': 'bar'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-05T06:08:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'bar'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-06T00:00:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'foo'}
    assert resp.json['workflow']['fields'] == {'plop': 'bar'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-06T00:00:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'bar'}

    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-07T00:00:00+01:00'})
    assert resp.json['fields'] == {'foobar': 'foo'}
    assert resp.json['workflow']['fields'] == {'plop': 'bar'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-07T00:00:00+01:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'foo'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'bar'}

    # check with other TZ
    resp = app.get(
        '/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T04:04:00+02:00'}, status=400
    )
    assert resp.json['err_desc'] == 'No data found for this datetime.'
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T04:04:00+02:00'})
    assert len(resp.json) == 0
    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T04:05:00+02:00'})
    assert resp.json['fields'] == {'foobar': 'bar'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T04:05:00+02:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'bar'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}

    # check without TZ
    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T03:04:00'}, status=400)
    assert resp.json['err_desc'] == 'No data found for this datetime.'
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T03:04:00'})
    assert len(resp.json) == 0
    resp = app.get('/api/forms/test/%s/' % formdata.id, params={'at': '2022-01-02T03:05:00'})
    assert resp.json['fields'] == {'foobar': 'bar'}
    assert resp.json['workflow']['fields'] == {'plop': 'foo'}
    resp = app.get('/api/forms/test/list/', params={'full': 'on', 'at': '2022-01-02T03:05:00'})
    assert len(resp.json) == 1
    assert resp.json[0]['fields'] == {'foobar': 'bar'}
    assert resp.json[0]['workflow']['fields'] == {'plop': 'foo'}
