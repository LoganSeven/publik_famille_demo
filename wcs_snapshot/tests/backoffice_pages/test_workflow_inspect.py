import io

import pytest
from quixote.http_request import Upload as QuixoteUpload

from wcs import fields
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.create_formdata import Mapping
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    pub.set_app_dir(req)
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_workflow_inspect_page(pub):
    admin = create_superuser(pub)

    workflow = Workflow(name='blah')
    st1 = workflow.add_status('Status1')
    jump = st1.add_action('jump', id='_jump')
    jump.timeout = '=86400'
    jump.mode = 'timeout'
    jump.status = 'finished'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert '=86400' in resp.text

    jump.timeout = '82800'
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert '23 hours' in resp.text

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.workflow_roles = {'_receiver': 1}
    target_formdef.backoffice_submission_roles = admin.roles[:]
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
        fields.FileField(id='1', label='file', varname='foo_file'),
    ]

    st2 = workflow.add_status('Status2')

    target_formdef.store()
    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.draft = True
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.user_association_mode = 'keep-user'
    create_formdata.backoffice_submission = True
    create_formdata.mappings = [
        Mapping(field_id='0', expression='=form_var_toto_string'),
        Mapping(field_id='1', expression='=form_var_toto_file_raw'),
        Mapping(field_id='2', expression='=form_var_foobar_raw'),
    ]
    workflow.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert (
        '<ul class="mappings"><li>string → =form_var_toto_string</li>'
        '<li>file → =form_var_toto_file_raw</li>'
        '<li>#2 → =form_var_foobar_raw</li></ul>'
    ) in resp.text

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Foo Bar 1', varname='foo_bar'),
        fields.StringField(id='bo2', label='Foo Bar 2', varname='foo_bar'),
        fields.StringField(id='bo3', label='Foo Bar 3', varname='foo_bar'),
    ]
    setbo = st2.add_action('set-backoffice-fields')
    setbo.fields = [
        {'field_id': 'bo1', 'value': 'go'},
        {'field_id': 'bo2', 'value': ''},
        {'field_id': 'bo3', 'value': None},
        {'field_id': 'unknown', 'value': 'foobar'},
    ]
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert (
        '<ul class="fields"><li>Foo Bar 1 → go</li>'
        '<li>Foo Bar 2 → </li>'
        '<li>Foo Bar 3 → None</li>'
        '<li>#unknown → foobar</li></ul>'
    ) in resp.text

    st3 = workflow.add_status('Status3', 'st3')
    export_to = st3.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    upload = QuixoteUpload('/foo/test.odt', content_type='application/vnd.oasis.opendocument.text')
    upload.fp = io.BytesIO()
    upload.fp.write(b'HELLO WORLD')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    workflow.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert (
        '<span class="parameter">Model:</span> File</li>'
        '<li class="parameter-model_file">'
        '<a href="status/st3/items/_export_to/?file=model_file">test.odt</a></li>'
    ) in resp.text


def test_workflow_user_roles_inspect_page(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    wf = Workflow(name='blah')
    st1 = wf.add_status('New')
    add_role = st1.add_action('add_role')
    remove_role = st1.add_action('remove_role')
    wf.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % wf.id)
    assert '<span class="parameter">Role:</span>' not in resp

    add_role.role_id = 'foobar'
    remove_role.role_id = 'barfoo'
    wf.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % wf.id)
    assert '<span class="parameter">Role to Add:</span> unknown - foobar' in resp
    assert '<span class="parameter">Role to Remove:</span> unknown - barfoo' in resp

    role_a = pub.role_class(name='role A')
    role_a.store()
    role_b = pub.role_class(name='role B')
    role_b.store()
    add_role.role_id = role_a.id
    remove_role.role_id = role_b.id
    wf.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % wf.id)
    assert '<span class="parameter">Role to Add:</span> role A' in resp
    assert '<span class="parameter">Role to Remove:</span> role B' in resp


def test_workflow_options_inspect_page(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = []
    carddef.store()

    workflow = Workflow(name='blah')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': 'carddef:%s' % carddef.url_name})
    )
    # missing carddef
    workflow.variables_formdef.fields.append(
        fields.ItemField(id='2', label='item', data_source={'type': 'carddef:foo'})
    )
    workflow.store()

    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert 'card model: card title' in resp.text
    assert 'deleted card model' in resp.text


def test_workflow_inspect_page_trigger(pub):
    create_superuser(pub)

    workflow = Workflow(name='blah')
    ac1 = workflow.add_global_action('action')
    trigger1 = ac1.triggers[0]
    trigger2 = ac1.append_trigger('timeout')
    trigger3 = ac1.append_trigger('webservice')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert str(trigger1.render_as_line()) in resp.text
    assert str(trigger2.render_as_line()) in resp.text
    assert str(trigger3.render_as_line()) in resp.text

    assert 'Allow as mass action: Yes' in resp.text
    trigger1.allow_as_mass_action = False
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert 'Allow as mass action: No' in resp.text

    assert 'String / Template with reference date' not in resp.text
    trigger2.anchor = 'template'
    trigger2.anchor_template = 'XXX'
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert 'String / Template with reference date' in resp.text

    assert 'Roles required to trigger using HTTP hook: None' in resp.text
    trigger3.roles = ['_submitter']
    workflow.store()
    resp = app.get('/backoffice/workflows/%s/inspect' % workflow.id)
    assert 'Roles required to trigger using HTTP hook: User' in resp.text
