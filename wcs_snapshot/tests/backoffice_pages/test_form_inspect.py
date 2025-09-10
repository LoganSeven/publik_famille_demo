import datetime
import io
import json
import os
import re

import pytest
import responses
from django.utils.timezone import localtime
from pyquery import PyQuery

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.errors import ConnectionError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.create_formdata import Mapping
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import (
    AttachmentEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowVariablesFieldsFormDef,
)
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


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


class IHateUnicode:
    def __unicode__(self):
        raise Exception('HATE!!')

    def __repr__(self):
        return 'ok'


def test_inspect_page(pub, local_user):
    create_user(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String', varname='string'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.ItemField(
            id='2',
            label='2nd field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.ItemField(id='3', label='3rd field', data_source=datasource, varname='foo'),
        fields.FileField(id='4', label='file field', varname='file'),
        fields.BlockField(
            id='5', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
        fields.StringField(id='6', label='Empty', varname='empty'),
        fields.NumericField(id='7', label='Numeric', varname='number'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    upload = PicklableUpload('hello.txt', content_type='text/plain')
    upload.receive([b'hello world'])
    formdata.data = {
        '1': 'FOO BAR 0',
        '2': 'baz',
        '2_display': 'baz',
        '3': 'C',
        '3_display': 'cc',
        # temper with field 3 structured values
        '3_structured': {
            'unicode': 'uné',
            'str_but_non_utf8': b'\xed\xa0\x00',  # not actually supposed to happen
            'non_unicode_convertible': IHateUnicode(),
            'very_long_string': '0' * 100000,
        },
        # add a PicklableUpload in field 4
        '4': upload,
        # block field
        '5': {
            'data': [
                {
                    '1': 'plop',
                },
                {
                    '1': 'poulpe',
                },
            ],
            'schema': {},  # not important here
        },
        '6': None,
        '0_display': 'plop poulpe',
        '7': 12,  # numeric
    }
    formdata.jump_status('new')
    formdata.user_id = local_user.id
    formdata.workflow_data = {
        'foo': {
            'bar_coin': 'yy',
            'errors': [
                'this one has a\xa0non-breaking space',
            ],
        },
        'foo_bar': 'xx',
    }
    formdata.store()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='_first_name', label='name'))
    user_formdef.fields.append(fields.StringField(id='3', label='test'))
    user_formdef.store()
    local_user.form_data = {'_first_name': 'toto', '3': 'nono'}
    local_user.set_attributes_from_formdata(local_user.form_data)
    local_user.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True), status=200)
    assert 'Data Inspector' not in resp.text
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=403)

    create_user(pub, is_admin=True)
    resp = app.get(formdata.get_url(backoffice=True), status=200)
    resp = resp.click('Data Inspector')

    assert '0' * 1000 in resp.text
    assert len(resp.text) < 100000
    pq = resp.pyquery.remove_namespaces()
    assert pq('[title="form_var_file"]').parents('li').children('div.value span').text() == 'hello.txt (file)'
    assert pq('[title="form_var_file"]').children('a').attr('title') == 'file field'
    assert (
        pq('[title="form_var_file"]').children('a').attr('href')
        == 'http://example.net/backoffice/forms/%s/fields/4/' % formdef.id
    )
    assert (
        pq('[title="form_var_file_raw"]').parents('li').children('div.value span').text()
        == 'hello.txt (file)'
    )
    assert len(pq('[title="form_var_file_raw"]').children('a')) == 0
    assert (
        pq('[title="form_var_number"]').parents('li').children('div.value span').text()
        == '12 (decimal number)'
    )
    assert pq('[title="form_var_foo_unicode"]').parents('li').children('div.value span').text() == 'uné'
    assert (
        pq('[title="form_var_foo_non_unicode_convertible"]')
        .parents('li')
        .children('div.value span')
        .text()
        .startswith('ok ')
    )
    assert (
        pq('[title="form_var_foo_str_but_non_utf8"]').parents('li').children('div.value span').text()
        == "b'\\xed\\xa0\\x00' (bytes)"
    )
    assert (
        pq('[title="form_workflow_data_foo_errors"]').parents('li').children('div.value span').text()
        == "['this one has a non-breaking space'] (list)"
    )
    assert pq('[title="form_var_blockdata"]').children('a').attr('title') == 'Block Data'
    assert (
        pq('[title="form_var_blockdata"]').children('a').attr('href')
        == 'http://example.net/backoffice/forms/%s/fields/5/' % formdef.id
    )
    assert pq('[title="form_var_blockdata_0_string"]').children('a').attr('title') == 'String'
    assert (
        pq('[title="form_var_blockdata_0_string"]').children('a').attr('href')
        == 'http://example.net/backoffice/forms/blocks/%s/1/' % block.id
    )
    assert pq('[title="form_var_blockdata_1_string"]').children('a').attr('title') == 'String'
    assert (
        pq('[title="form_var_blockdata_1_string"]').children('a').attr('href')
        == 'http://example.net/backoffice/forms/blocks/%s/1/' % block.id
    )
    assert pq('[title="form_var_empty"]').children('a').attr('title') == 'Empty'
    assert (
        pq('[title="form_var_empty"]').children('a').attr('href')
        == 'http://example.net/backoffice/forms/%s/fields/6/' % formdef.id
    )

    # don't show «unusable» variables
    assert 'form_f1' not in resp.text
    assert 'form_field_' not in resp.text
    assert 'form_user_field_' not in resp.text
    assert 'form_user_f3' not in resp.text
    assert 'form_user_f_' not in resp.text

    # make sure workflow data itself is not displayed, as it's available in
    # expanded variables
    assert not pq('[title="form_workflow_data"]')

    # but do display it if it has some invalid contents
    formdata.workflow_data['invalid key'] = 'foobar'
    formdata.store()
    resp = app.get(resp.request.url)
    assert resp.pyquery('[title="form_workflow_data"]')

    # check functions
    assert re.findall('Recipient.*foobar', resp.text)

    role = pub.role_class(name='plop')
    role.store()
    formdata.workflow_roles = {'_receiver': [role.id]}
    formdata.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert re.findall('Recipient.*plop', resp.text)

    workflow = Workflow.get_default_workflow()
    workflow.id = None
    workflow.roles.update({'_plop': 'New Function'})
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Foo bar', varname='foo_bar'),
    ]
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert re.findall('New Function.*unset', resp.text)

    formdata.workflow_roles = {'_receiver': [role.id], '_plop': ['123']}
    formdata.data['bo1'] = 'foobar42'
    formdata.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert re.findall('New Function.*(deleted)', resp.text)
    pq = resp.pyquery.remove_namespaces()
    assert pq('[title="form_var_foo_bar"]').parents('li').children('div.value span').text() == 'foobar42'
    assert pq('[title="form_var_foo_bar"]').children('a').attr('title') == 'Foo bar'
    assert (
        pq('[title="form_var_foo_bar"]').children('a').attr('href')
        == 'http://example.net/backoffice/workflows/%s/backoffice-fields/fields/bo1/' % workflow.id
    )

    # test form_attachments
    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart(
            'hello.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
        )
    ]
    formdata.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert resp.pyquery('[title="form_attachments"]').length == 0
    assert (
        resp.pyquery('[title="form_attachments_testfile"]').parents('li').children('div.value span').text()
        == 'hello.txt (file)'
    )
    assert (
        resp.pyquery('[title="form_attachments_testfile_content_type"]')
        .parents('li')
        .children('div.value span')
        .text()
        == 'text/plain'
    )
    assert (
        '/attachment?'
        in resp.pyquery('[title="form_attachments_testfile_url"]')
        .parents('li')
        .children('div.value span')
        .text()
    )
    assert resp.pyquery('[title="form_attachments_0_testfile"]').length == 0

    formdata.evolution[-1].parts.append(
        AttachmentEvolutionPart(
            'hello2.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
        )
    )
    formdata.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert (
        resp.pyquery('[title="form_attachments_testfile_base_filename"]')
        .parents('li')
        .children('div.value span')
        .text()
        == 'hello2.txt'
    )
    assert (
        resp.pyquery('[title="form_attachments_testfile_0_base_filename"]')
        .parents('li')
        .children('div.value span')
        .text()
        == 'hello.txt'
    )
    assert (
        resp.pyquery('[title="form_attachments_testfile_1_base_filename"]')
        .parents('li')
        .children('div.value span')
        .text()
        == 'hello2.txt'
    )

    # test tools
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert 'Test tool' in resp.text

    resp.form['test_mode'] = 'django-condition'
    resp.form['django-condition'] = 'form_name|length == 10'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text
    resp.form['django-condition'] = 'form_name|length == 5'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-false' in resp.text
    resp.form['django-condition'] = 'foo bar'
    resp = resp.form.submit()
    assert 'Condition result' not in resp.text
    assert 'TemplateSyntaxError: Unused' in resp.text

    resp.form['django-condition'] = 'form_name|length == 5'
    ajax_resp = app.post(
        '%sinspect-tool' % formdata.get_url(backoffice=True), params=resp.form.submit_fields()
    )
    assert ajax_resp.text.startswith('<div class="test-tool-result')

    resp.form['_form_id'].value = ajax_resp.headers['X-Form-Token']
    ajax_resp = app.post(
        '%sinspect-tool' % formdata.get_url(backoffice=True), params=resp.form.submit_fields()
    )
    assert ajax_resp.text.startswith('<div class="test-tool-result')

    # CSRF
    ajax_resp = app.post(
        '%sinspect-tool' % formdata.get_url(backoffice=True), params=resp.form.submit_fields()
    )
    assert ajax_resp.text == ''

    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    resp.form['test_mode'] = 'template'
    resp.form['template'] = '{{ form_name }}'
    resp = resp.form.submit()
    assert 'Template rendering' in resp.text
    assert '<div class="test-tool-result-plain">form title</div>' in resp.text
    assert 'HTML Source' not in resp.text
    assert 'rendered as an object' not in resp.text

    resp.form['template'] = '<p>{{ form_name }}</p>'
    resp = resp.form.submit()
    assert 'Template rendering' in resp.text
    assert '<p>form title</p>' in resp.text
    assert 'HTML Source' in resp.text
    assert 'rendered as an object' not in resp.text

    resp.form['template'] = '{{ form_var_file }}'
    resp = resp.form.submit()
    assert 'Template rendering' in resp.text
    assert '<div class="test-tool-result-plain">hello.txt</div>' in resp.text
    assert 'rendered as an object' in resp.text
    assert '<div class="test-tool-result-plain">hello.txt (file)</div>' in resp.text
    assert 'HTML Source' not in resp.text

    resp.form['template'] = '{{ form_var_file_raw.get_content }}'  # will give bytes
    resp = resp.form.submit()
    assert 'Template rendering' in resp.text
    assert '<div class="test-tool-result-plain">b\'' in resp.text
    assert 'rendered as an object' in resp.text
    assert '<div class="test-tool-result-plain">b\'' in resp.text
    assert '(bytes)' in resp.text
    assert 'HTML Source' not in resp.text

    resp.form['template'] = '{% for x in 1 %}ok{% endfor %}'
    resp = resp.form.submit()
    assert 'Failed to evaluate template' in resp.text
    assert 'TypeError' in resp.text
    resp.form['template'] = '{% for x in 1 %}ok{% end %}'
    resp = resp.form.submit()
    assert 'syntax error' in resp.text
    assert 'Invalid block tag' in resp.text
    resp.form['template'] = ''

    resp.form['test_mode'] = 'html_template'
    resp.form['html_template'] = '<p>{{ form_name }}</p>'
    resp = resp.form.submit()
    resp = resp.form.submit()
    assert 'Template rendering' in resp.text
    assert '<p>form title</p>' in resp.text
    assert 'HTML Source' in resp.text
    resp.form['html_template'] = '{% for x in 1 %}ok{% endfor %}'
    resp = resp.form.submit()
    assert 'Failed to evaluate HTML template' in resp.text
    assert 'TypeError' in resp.text
    resp.form['html_template'] = '{% for x in 1 %}ok{% end %}'
    resp = resp.form.submit()
    assert 'syntax error' in resp.text
    assert 'Invalid block tag' in resp.text
    resp.form['html_template'] = '<p>hello</p><script>alert("hello")</script>'
    resp = resp.form.submit()
    assert (
        str(resp.pyquery('.test-tool-result-html'))
        == '<div class="test-tool-result-html"><p>hello</p>alert("hello")</div>'
    )
    assert (
        str(resp.pyquery('.test-tool-result-plain'))
        == '<pre class="test-tool-result-plain">&lt;p&gt;hello&lt;/p&gt;alert("hello")</pre>'
    )

    # check errors are not logged, and are nicely reported
    LoggedError.wipe()
    resp.form['test_mode'] = 'django-condition'
    resp.form['django-condition'] = 'form_objects|filter_by:"not a field"|filter_value:"test"'
    resp = resp.form.submit()
    assert resp.pyquery('#test-tool-result p:last-child').text() == 'Invalid filter "not a field"'
    assert LoggedError.count() == 0

    resp.form['django-condition'] = 'form_objects|first|get:0'
    resp = resp.form.submit()
    assert resp.pyquery('#test-tool-result p:last-child').text() == '|get called with invalid key (0)'
    assert LoggedError.count() == 0

    # check there's a custom hint when a template is used as condition
    resp.form['test_mode'] = 'django-condition'
    resp.form['django-condition'] = '{% if True %}hello{% endif %}'
    resp = resp.form.submit()
    assert (
        resp.pyquery('#test-tool-result p.hint').text()
        == 'This tool expects a condition, not a complete template.'
    )


def test_inspect_page_with_related_objects(pub):
    user = create_user(pub, is_admin=True)

    FormDef.wipe()
    CardDef.wipe()

    # test ExternalWorkflowGlobalAction
    external_wf = Workflow(name='External Workflow')
    st1 = external_wf.add_status(name='New')
    action = external_wf.add_global_action('Delete', 'delete')
    action.add_action('remove')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'delete'
    external_wf.store()

    external_formdef = FormDef()
    external_formdef.name = 'External Form'
    external_formdef.fields = [
        fields.StringField(id='0', label='string', varname='form_string'),
    ]
    external_formdef.workflow = external_wf
    external_formdef.store()

    external_carddef = CardDef()
    external_carddef.name = 'External Card'
    external_carddef.fields = [
        fields.StringField(id='0', label='string', varname='card_string'),
    ]
    external_carddef.backoffice_submission_roles = user.roles
    external_carddef.workflow = external_wf
    external_carddef.store()

    wf = Workflow(name='External actions')
    st1 = wf.add_status('Create external formdata')

    # add a message to history, to check it doesn't interfer when searching for
    # linked data.
    register_comment = st1.add_action('register-comment', id='_register')
    register_comment.comment = '<p>test</p>'

    create_formdata = st1.add_action('create_formdata', id='_create_form')
    create_formdata.action_label = 'create linked form'
    create_formdata.formdef_slug = external_formdef.url_name
    create_formdata.varname = 'created_form'
    mappings = [Mapping(field_id='0', expression='{{ form_var_string }}')]
    create_formdata.mappings = mappings

    create_carddata = st1.add_action('create_carddata', id='_create_card')
    create_carddata.action_label = 'create linked card'
    create_carddata.formdef_slug = external_carddef.url_name
    create_carddata.varname = 'created_card'
    create_carddata.mappings = mappings

    global_action = wf.add_global_action('Delete external linked object', 'delete')
    action = global_action.add_action('external_workflow_global_action')
    action.slug = 'formdef:%s' % external_formdef.url_name
    action.trigger_id = 'action:%s' % trigger.identifier
    wf.store()

    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
    ]
    formdef.workflow = wf
    formdef.store()

    carddef = CardDef()
    carddef.name = 'External action card'
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow = wf
    carddef.store()

    assert formdef.data_class().count() == 0
    assert carddef.data_class().count() == 0
    assert external_formdef.data_class().count() == 0
    assert external_carddef.data_class().count() == 0

    formdata = formdef.data_class()()
    formdata.data = {'0': 'test form'}
    formdata.user = user
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    assert formdef.data_class().count() == 1
    assert formdef.data_class().get(1).relations_data == {
        'formdef:external-form': ['1'],
        'carddef:external-card': ['1'],
    }
    assert carddef.data_class().count() == 0
    # related form and card
    assert external_formdef.data_class().count() == 1
    assert external_formdef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}
    assert external_carddef.data_class().count() == 1
    assert external_carddef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/external-action-form/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    # related form and card
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/management/external-form/1/"]').text()
        == 'External Form #1-1 (Evolution)'
    )
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/data/external-card/1/"]').text()
        == 'External Card #1-1 (Evolution)'
    )
    # check related form
    resp = app.get('/backoffice/management/external-form/1/')
    assert '<h3>Original form</h3>' in resp.text
    assert resp.pyquery('.extra-context--orig-data').text() == 'External action form #2-1'
    assert (
        resp.pyquery('.extra-context--orig-data').attr.href
        == 'http://example.net/backoffice/management/external-action-form/1/'
    )
    resp = app.get('/backoffice/management/external-form/1/inspect')
    # parent
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/management/external-action-form/1/"]').text()
        == 'External action form #2-1 (Parent)'
    )
    # check related card
    resp = app.get('/backoffice/data/external-card/1/')
    assert '<h3>Original form</h3>' in resp.text
    assert resp.pyquery('.extra-context--orig-data').text() == 'External action form #2-1'
    assert (
        resp.pyquery('.extra-context--orig-data').attr.href
        == 'http://example.net/backoffice/management/external-action-form/1/'
    )
    resp = app.get('/backoffice/data/external-card/1/inspect')
    # parent
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/management/external-action-form/1/"]').text()
        == 'External action form #2-1 (Parent)'
    )

    external_formdef.data_class().wipe()
    external_carddef.data_class().wipe()

    # missing form/card data
    resp = app.get('/backoffice/management/external-action-form/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    assert [x.text for x in resp.pyquery('#inspect-relations a[href=""]')] == [
        'Linked "External Form" object by id 1 (Evolution - not found)',
        'Linked "External Card" object by id 1 (Evolution - not found)',
    ]

    formdef.data_class().wipe()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'test card'}
    carddata.user = user
    carddata.store()
    carddata.just_created()
    carddata.perform_workflow()

    assert formdef.data_class().count() == 0
    assert carddef.data_class().count() == 1
    assert carddef.data_class().get(1).relations_data == {
        'carddef:external-card': ['2'],
        'formdef:external-form': ['2'],
    }
    # related form and card
    assert external_formdef.data_class().count() == 1
    assert external_formdef.data_class().get(2).relations_data == {'carddef:external-action-card': ['1']}
    assert external_carddef.data_class().count() == 1
    assert external_carddef.data_class().get(2).relations_data == {'carddef:external-action-card': ['1']}

    resp = app.get('/backoffice/data/external-action-card/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    # related form and card
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/management/external-form/2/"]').text()
        == 'External Form #1-2 (Evolution)'
    )
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/data/external-card/2/"]').text()
        == 'External Card #1-2 (Evolution)'
    )
    # check related form
    resp = app.get('/backoffice/management/external-form/2/')
    assert '<h3>Original card</h3>' in resp.text
    assert resp.pyquery('.extra-context--orig-data').text() == 'External action card #2-1'
    assert (
        resp.pyquery('.extra-context--orig-data').attr.href
        == 'http://example.net/backoffice/data/external-action-card/1/'
    )
    resp = app.get('/backoffice/management/external-form/2/inspect')
    # parent
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/data/external-action-card/1/"]').text()
        == 'External action card #2-1 (Parent)'
    )
    # check related card
    resp = app.get('/backoffice/data/external-card/2/')
    assert '<h3>Original card</h3>' in resp.text
    assert resp.pyquery('.extra-context--orig-data').text() == 'External action card #2-1'
    assert (
        resp.pyquery('.extra-context--orig-data').attr.href
        == 'http://example.net/backoffice/data/external-action-card/1/'
    )
    resp = app.get('/backoffice/data/external-card/2/inspect')
    # parent
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/data/external-action-card/1/"]').text()
        == 'External action card #2-1 (Parent)'
    )

    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()
    WorkflowTrace.wipe()

    # test linked Card in datasource
    wf = Workflow(name='WF')
    st1 = wf.add_status('NEW')
    wf.store()
    wfc = Workflow(name='WFC')
    wfc.add_status('NEW')
    wfc.store()

    carddef = CardDef()
    carddef.name = 'CARD A'
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow = wfc
    carddef.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'FORM A'
    formdef.fields = [
        fields.ItemField(id='0', label='Card', varname='card', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text'}
    carddata.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': '1'}
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    assert formdef.data_class().count() == 1
    assert formdef.data_class().get(1).relations_data == {'carddef:card-a': ['1']}
    assert carddef.data_class().count() == 1
    assert carddef.data_class().get(1).relations_data == {}

    resp = app.get('/backoffice/management/form-a/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    # related card
    assert (
        resp.pyquery('#inspect-relations a[href$="/backoffice/data/card-a/1/"]').text()
        == 'CARD A #1-1 (Data Source - in field with identifier: card)'
    )

    # missing carddata
    carddef.data_class().wipe()
    resp = app.get('/backoffice/management/form-a/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    assert (
        resp.pyquery('#inspect-relations a[href=""]').text()
        == 'Linked "CARD A" object by id 1 (Data Source - in field with identifier: card - not found)'
    )

    # missing carddef
    CardDef.wipe()
    resp = app.get('/backoffice/management/form-a/1/inspect')
    assert 'Related Forms/Cards' in resp.text
    assert (
        resp.pyquery('#inspect-relations a[href=""]').text()
        == 'Linked object def by id card-a (Data Source - in field with identifier: card - not found)'
    )


def test_inspect_page_actions_traces(pub):
    user = create_user(pub, is_admin=True)

    FormDef.wipe()
    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    CardDef.wipe()
    target_carddef = CardDef()
    target_carddef.name = 'target card'
    target_carddef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_carddef.store()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    action = workflow.add_global_action('Timeout Test')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'creation'
    trigger.timeout = '2'

    global_api_trigger_action = workflow.add_global_action('Global API Trigger Test')
    global_api_trigger_action.add_action('modify_criticality')
    global_api_trigger_action.append_trigger('webservice')

    st1 = workflow.possible_status[1]

    create_formdata = st1.add_action('create_formdata', id='_create', prepend=True)
    create_formdata.varname = 'create_formdata'
    create_formdata.formdef_slug = target_formdef.url_name
    mappings = [Mapping(field_id='0', expression='foo bar')]
    create_formdata.mappings = mappings

    create_carddata = st1.add_action('create_carddata', id='_create_card')
    create_carddata.action_label = 'create linked card'
    create_carddata.formdef_slug = target_carddef.url_name
    create_carddata.varname = 'created_card'
    create_carddata.mappings = mappings

    edit_carddata = st1.add_action('edit_carddata', id='_edit_card')
    edit_carddata.action_label = 'edit linked card'
    edit_carddata.formdef_slug = target_carddef.url_name
    edit_carddata.varname = 'edited_card'
    edit_carddata.mappings = [Mapping(field_id='0', expression='foo bar blah')]

    edit_carddata2 = st1.add_action('edit_carddata', id='_edit_card2')
    edit_carddata2.target_mode = 'manual'
    edit_carddata2.target_id = '{{ unknown }}'
    edit_carddata2.action_label = 'edit linked card with wrong target_id'
    edit_carddata2.formdef_slug = target_carddef.url_name
    edit_carddata2.varname = 'edited_card_wrong_target_id'
    edit_carddata2.mappings = [Mapping(field_id='0', expression='foo bar blah')]

    choice = st1.items[2]
    assert choice.key == 'choice'
    choice.by = ['logged-users']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.record_workflow_event('frontoffice-created')
    formdata.perform_workflow()
    formdata.store()
    app = login(get_app(pub))
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_accept')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-accepted'

    # change receipt time to get global timeout to run
    formdata.receipt_time = localtime() - datetime.timedelta(days=3)
    formdata.store()
    pub.apply_global_action_timeouts()
    formdata.refresh_from_storage()
    assert formdata.get_criticality_level_object().name == 'yellow'

    resp = app.get(formdata.get_url(backoffice=True), status=200)
    resp = resp.click('Data Inspector')
    assert '>Actions Tracing</' in resp
    assert [PyQuery(x).text() for x in resp.pyquery('#inspect-timeline .event')] == [
        'Created (frontoffice submission)',
        'Continuation',
        'Created form - target form #1-1',
        'Created card - target card #1-1',
        'Edited card - target card #1-1',
        'Action button - Manual Jump Accept',
        'Global action timeout',
    ]
    assert [x.text for x in resp.pyquery('#inspect-timeline strong')] == ['Just Submitted', 'New', 'Accepted']
    assert [x.text for x in resp.pyquery('#inspect-timeline a.tracing-link') if x.text] == [
        'Email',
        'Email',
        'Automatic Jump',
        'New Form Creation',
        'Create Card Data',
        'Edit Card Data',
        'Edit Card Data',
        'Email',
        'Email',
        'Criticality Levels',
    ]
    event_links = [x.attrib['href'] for x in resp.pyquery('#inspect-timeline .event a')]
    assert event_links == [
        'http://example.net/backoffice/management/target-form/1/',  # Created form
        'http://example.net/backoffice/data/target-card/1/',  # Created card
        'http://example.net/backoffice/data/target-card/1/',  # Edited card
        'http://example.net/backoffice/workflows/2/status/new/items/_accept/',  # Accept manual jump
        'http://example.net/backoffice/workflows/2/global-actions/1/#trigger-%s' % trigger.id,
    ]
    # check all links are valid
    for link in event_links:
        app.get(link)
    assert [x.text for x in resp.pyquery('#inspect-timeline .event a') if x.text] == [
        'Created form - target form #1-1',
        'Created card - target card #1-1',
        'Edited card - target card #1-1',
        'Action button - Manual Jump Accept',
        'Global action timeout',
    ]
    assert [x.text for x in resp.pyquery('#inspect-timeline .event-error')] == ['Nothing edited']
    action_links = [x.attrib['href'] for x in resp.pyquery('#inspect-timeline a.tracing-link')]
    assert len(action_links) == 13
    assert action_links[0] == 'http://example.net/backoffice/workflows/2/status/just_submitted/'
    assert (
        action_links[1]
        == 'http://example.net/backoffice/workflows/2/status/just_submitted/items/_notify_new_receiver_email/'
    )
    assert action_links[-8] == 'http://example.net/backoffice/workflows/2/status/new/items/_create/'
    assert action_links[-7] == 'http://example.net/backoffice/workflows/2/status/new/items/_create_card/'
    assert action_links[-6] == 'http://example.net/backoffice/workflows/2/status/new/items/_edit_card/'
    assert action_links[-5] == 'http://example.net/backoffice/workflows/2/status/new/items/_edit_card2/'
    assert action_links[-1] == 'http://example.net/backoffice/workflows/2/global-actions/1/items/1/'

    # check details are available
    assert 'Email (to Recipient)' in [
        x.text_content().split(' ', 2)[-1] for x in resp.pyquery('#inspect-timeline li')
    ]

    # check links on target formdata
    target_formdata = target_formdef.data_class().select()[0]
    resp = app.get(target_formdata.get_url(backoffice=True) + 'inspect')
    assert '>Actions Tracing</' in resp
    assert [x.text for x in resp.pyquery('#inspect-timeline .event a')] == ['Created (by workflow action)']
    event_links = [x.attrib['href'] for x in resp.pyquery('#inspect-timeline .event a')]
    assert (
        event_links[0] == 'http://example.net/backoffice/workflows/2/status/new/items/_create/'
    )  # link on external workflow

    # and no crash if a linked carddef is removed
    target_carddef.remove_self()
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect', status=200)
    assert [x.text for x in resp.pyquery('#inspect-timeline .event a') if x.text] == [
        'Created form - target form #1-1',
        'Created card - deleted',
        'Edited card - deleted',
        'Action button - Manual Jump Accept',
        'Global action timeout',
    ]

    # check link when a global action is called
    formdata.record_workflow_event('global-api-trigger', global_action_id=global_api_trigger_action.id)
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect')
    assert resp.pyquery('#inspect-timeline a.event--global-action').text() == 'Global API Trigger Test'
    resp.click('Global API Trigger Test')  # check link is valid

    # and there's no crash when part of the workflow changes
    workflow.global_actions = []
    workflow.store()
    app.get(formdata.get_url(backoffice=True) + 'inspect')
    workflow.possible_status[0].items = []
    workflow.store()
    app.get(formdata.get_url(backoffice=True) + 'inspect')
    workflow.possible_status = []
    workflow.store()
    app.get(formdata.get_url(backoffice=True) + 'inspect')

    # delete external workflow
    workflow.remove_self()
    resp = app.get(target_formdata.get_url(backoffice=True) + 'inspect')
    assert '>Actions Tracing</' in resp
    assert [x.text for x in resp.pyquery('#inspect-timeline .event a')] == ['Created (by workflow action)']
    event_links = [x.attrib['href'] for x in resp.pyquery('#inspect-timeline .event a')]
    assert event_links[0] == '#missing-_create'


def test_inspect_page_actions_traces_interactive_action_jump(pub):
    FormDef.wipe()
    Workflow.wipe()

    create_user(pub, is_admin=True)

    workflow = Workflow(name='test')
    st0 = workflow.add_status('st0')
    action = st0.add_action('register-comment')
    action.comment = 'test st0'
    st1 = workflow.add_status('st1')
    action = st1.add_action('register-comment')
    action.comment = 'test st1'

    global_action = workflow.add_global_action('global action')
    trigger = global_action.triggers[0]
    trigger.roles = ['_receiver']
    commentable = global_action.add_action('commentable')
    commentable.by = trigger.roles
    jump = global_action.add_action('choice')
    jump.label = 'JUMP'
    jump.by = trigger.roles
    jump.status = str(st1.id)
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form'
    formdef.workflow = workflow
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    resp.forms['wf-actions']['comment'] = 'plop'
    resp = resp.forms['wf-actions'].submit(f'button{jump.id}')
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st1.id}'

    resp = app.get(formdata.get_url(backoffice=True), status=200)
    resp = resp.click('Data Inspector')
    assert [
        PyQuery(x).text() for x in resp.pyquery('#inspect-timeline .event, #inspect-timeline .tracing-link')
    ] == ['Global action (interactive)', 'global action', 'st1', 'Continuation', 'History Message']
    event_links = [PyQuery(x).attr.href for x in resp.pyquery('#inspect-timeline a')]
    assert event_links == [
        'http://example.net/backoffice/workflows/1/global-actions/1/',  # global action
        'http://example.net/backoffice/workflows/1/status/2/',  # status 2
        'http://example.net/backoffice/workflows/1/status/2/items/1/',  # history message action
    ]
    # check all links are valid
    for link in event_links:
        app.get(link)


def test_inspect_page_missing_carddef_error(pub):
    create_user(pub, is_admin=True)
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = login(get_app(pub)).get('%sinspect' % formdata.get_url(backoffice=True), status=200)

    resp.form['test_mode'] = 'template'
    resp.form['template'] = '{{ cards|objects:"XXX" }}'
    resp = resp.form.submit()
    assert 'Failed to evaluate template' in resp.text
    assert '|objects with invalid reference (\'XXX\')' in resp.text


def test_inspect_page_draft_formdata(pub, local_user):
    create_user(pub, is_admin=True)

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.status = 'draft'
    formdata.store()

    app = login(get_app(pub))
    app.get(formdata.get_url(backoffice=True) + 'inspect', status=403)

    pub.site_options.set('options', 'allow-draft-inspect', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app.get(formdata.get_url(backoffice=True) + 'inspect', status=200)


def test_inspect_page_map_field(pub, local_user):
    create_user(pub)
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.MapField(id='1', label='1st field', varname='map1'),
        fields.MapField(id='2', label='2nd field', varname='map2'),
        fields.MapField(id='3', label='3rd field', varname='map3'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '1': {'lat': 1.2345, 'lon': 6.789},  # valid value
        '2': None,  # empty value
    }
    formdata.jump_status('new')
    formdata.store()

    create_user(pub, is_admin=True)
    resp = login(get_app(pub)).get('%sinspect' % formdata.get_url(backoffice=True))
    assert resp.pyquery('[title="form_var_map1"]')
    assert resp.pyquery('[title="form_var_map1_lat"]')
    assert resp.pyquery('[title="form_var_map1_lon"]')
    assert resp.pyquery('[title="form_var_map2"]')
    assert not resp.pyquery('[title="form_var_map2_lat"]')
    assert not resp.pyquery('[title="form_var_map2_lon"]')


def test_inspect_page_time_range_field(pub, local_user):
    create_user(pub)
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.TimeRangeField(id='1', label='1st field', varname='range1'),
        fields.MapField(id='2', label='2nd field', varname='range2'),
        fields.MapField(id='3', label='3rd field', varname='range3'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '1': {
            'api': {'fillslot_url': 'https://chrono.dev.publik.love/xxx/'},
            'start_datetime': '2025-06-27 10:30',
            'end_datetime': '2025-06-27 11:30',
        },
        '1_display': 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.',
        '2': None,  # empty value
    }
    formdata.jump_status('new')
    formdata.store()

    create_user(pub, is_admin=True)
    resp = login(get_app(pub)).get('%sinspect' % formdata.get_url(backoffice=True))
    assert resp.pyquery('[title="form_var_range1"]')
    assert resp.pyquery('[title="form_var_range1_start_datetime"]')
    assert resp.pyquery('[title="form_var_range1_end_datetime"]')
    assert resp.pyquery('[title="form_var_range1_api_fillslot_url"]')

    assert 'https://chrono.dev.publik.love/xxx/' in resp.text
    assert '2025-06-27 10:30' in resp.text
    assert '2025-06-27 11:30' in resp.text
    assert 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.' in resp.text

    assert resp.pyquery('[title="form_var_range2"]')
    assert not resp.pyquery('[title="form_var_range2_start_datetime"]')
    assert resp.pyquery('[title="form_var_range3"]')
    assert not resp.pyquery('[title="form_var_range3_start_datetime"]')


def test_inspect_page_lazy_list(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    for value in ('foo', 'bar', 'baz'):
        formdata = formdef.data_class()()
        formdata.data = {'1': value}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    create_user(pub, is_admin=True)
    resp = login(get_app(pub)).get('%sinspect' % formdata.get_url(backoffice=True))

    resp.form['test_mode'] = 'template'
    resp.form['template'] = '{{ form_objects|order_by:"string"|getlist:"string" }}'
    resp = resp.form.submit()
    assert 'rendered as an object' in resp.text
    assert 'Also rendered as an iterable' not in resp.text
    assert resp.pyquery('.test-tool-lazylist-details li:first-child').text() == 'Number of items: 3'
    assert resp.pyquery('.test-tool-lazylist-details li:last-child').text() == 'First items: bar, baz, foo'


def test_inspect_page_iterable(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [
        fields.ItemsField(id='1', label='Items', varname='items', items=['foo1', 'foo2', 'foo3'])
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': ['foo1', 'foo3'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()

    create_user(pub, is_admin=True)
    app = login(get_app(pub))
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True))

    resp.form['test_mode'] = 'template'
    resp.form['template'] = '{{ form_var_items }}'
    resp = resp.form.submit()
    assert 'rendered as an object' in resp.text
    assert 'Also rendered as an iterable' in resp.text
    assert [PyQuery(result).text() for result in resp.pyquery('.test-tool-result-plain')] == [
        'foo1, foo3',
        'foo1, foo3 (string)',
        "['foo1', 'foo3'] (list)",
    ]

    # with carddef datasource
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [fields.StringField(id='1', label='First name', varname='firstname')]
    carddef.digest_templates = {'default': '{{ form_var_firstname }}'}
    carddef.store()
    carddef.data_class().wipe()
    carddatas = []
    for i in range(0, 3):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': 'foo%s' % i,
        }
        carddata.just_created()
        carddata.store()
        carddatas.append(carddata)

    formdef.fields[0].data_source = {'type': 'carddef:%s' % carddef.url_name}
    formdef.fields[0].items = None
    formdef.store()
    formdata.data = {
        '1': ['1', '3'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.store()

    resp = resp.form.submit()
    assert 'rendered as an object' in resp.text
    assert 'Also rendered as an iterable' in resp.text
    results = [PyQuery(result).text() for result in resp.pyquery('.test-tool-result-plain')]
    assert len(results) == 3
    assert results[0] == 'foo0, foo2'
    assert results[1] == 'foo0, foo2 (string)'
    assert '[<wcs.variables.LazyFormData object' in results[2]
    assert '] (list)' in results[2]

    # with json value
    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'b', 'text': 'baker', 'extra': 'plop'},
                {'id': 'c', 'text': 'cook', 'extra': 'plop2'},
                {'id': 'l', 'text': 'lawyer', 'extra': 'plop3'},
            ]
        ),
    }
    formdef.fields[0].data_source = datasource
    formdef.store()
    formdata.data = {
        '1': ['b', 'l'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.store()

    resp = resp.form.submit()
    assert 'rendered as an object' in resp.text
    assert 'Also rendered as an iterable' in resp.text
    assert [PyQuery(result).text() for result in resp.pyquery('.test-tool-result-plain')] == [
        'baker, lawyer',
        'baker, lawyer (string)',
        "[{'id': 'b', 'text': 'baker', 'extra': 'plop'}, {'id': 'l', 'text': 'lawyer', 'extra': 'plop3'}] (list)",
    ]

    # check with block field
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'Child'
    block.fields = [
        fields.StringField(id='123', required='required', label='First name', varname='firstname')
    ]
    block.digest_template = '{{ child_var_firstname }}'
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [fields.BlockField(id='1', label='Children', block_slug='child', varname='children')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'123': 'first1'}, {'123': 'first2'}],
            'schema': {'123': 'string'},
        },
        '1_display': 'foo, bar',
    }
    formdata.just_created()
    formdata.store()

    resp = app.get('%sinspect' % formdata.get_url(backoffice=True))
    resp.form['test_mode'] = 'template'
    resp.form['template'] = '{{ form_var_children }}'
    resp = resp.form.submit()
    assert 'rendered as an object' in resp.text
    assert 'Also rendered as an iterable' in resp.text
    results = [PyQuery(result).text() for result in resp.pyquery('.test-tool-result-plain')]
    assert len(results) == 3
    assert results[0] == 'foo, bar'
    assert (
        results[1] == "{'data': [{'123': 'first1'}, {'123': 'first2'}], 'schema': {'123': 'string'}} (dict)"
    )
    assert '[<wcs.variables.LazyBlockDataVar object' in results[2]
    assert '] (list)' in results[2]


def test_inspect_page_idp_role(pub):
    create_user(pub, is_admin=True)
    FormDef.wipe()

    app = login(get_app(pub))

    role = pub.role_class(name='plop')
    role.uuid = 'd4b59e1ffb204dfd99fd3760f4952999'
    role.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.cfg['idp'] = {'xxx': {'metadata_url': 'https://idp.example.net/idp/saml2/metadata'}}
    pub.write_cfg()

    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert resp.pyquery('[data-function-key="_receiver"] a').text() == 'plop'
    assert (
        resp.pyquery('[data-function-key="_receiver"] a').attr.href
        == 'https://idp.example.net/manage/roles/uuid:d4b59e1ffb204dfd99fd3760f4952999/'
    )


def test_inspect_page_form_option(pub):
    create_user(pub, is_admin=True)
    FormDef.wipe()

    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.add_status('st1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert 'form_option' not in resp.text

    wf.variables_formdef.fields = [
        fields.StringField(label='String test', varname='string_test'),
        fields.DateField(label='Date test', varname='date_test'),
    ]
    wf.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert (
        resp.pyquery('[title="form_option_string_test"]').parents('li').children('div.value span').text()
        == 'None (no value)'
    )

    wf.variables_formdef.fields[0].default_value = 'xxx'
    wf.variables_formdef.fields[1].default_value = '2024-03-20'
    wf.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert (
        resp.pyquery('[title="form_option_string_test"]').parents('li').children('div.value span').text()
        == 'xxx'
    )
    assert (
        resp.pyquery('[title="form_option_date_test"]').parents('li').children('div.value span').text()
        == '2024-03-20'
    )
    assert (
        resp.pyquery('[title="form_option_date_test_year"]').parents('li').children('div.value span').text()
        == '2024 (integer number)'
    )

    formdef.workflow_options = {'string_test': 'yyy', 'date_test': datetime.date(2024, 3, 21).timetuple()}
    formdef.store()
    resp = app.get('%sinspect' % formdata.get_url(backoffice=True), status=200)
    assert (
        resp.pyquery('[title="form_option_string_test"]').parents('li').children('div.value span').text()
        == 'yyy'
    )
    assert (
        resp.pyquery('[title="form_option_date_test"]').parents('li').children('div.value span').text()
        == '2024-03-21'
    )
    assert (
        resp.pyquery('[title="form_option_date_test_year"]').parents('li').children('div.value span').text()
        == '2024 (integer number)'
    )


def test_loop_template(pub):
    create_user(pub, is_admin=True)

    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('test')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemsField(
            id='1', label='1st field', items=['foo', 'bar', 'baz', 'qux', 'quux', 'corge'], varname='items'
        ),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = ['foo', 'bar', 'baz', 'qux', 'quux', 'corge']
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect')
    assert 'template_loop' not in resp.form.fields

    st1.loop_items_template = 'xxx'
    workflow.store()

    # template_loop left empty
    resp = app.get(formdata.get_url(backoffice=True) + 'inspect')
    resp.form['test_mode'] = 'template'
    resp.form['template_loop'] = ''
    resp.form['template'] = 'hello'
    resp = resp.form.submit()
    assert resp.pyquery('.test-tool-result-plain').text() == 'hello'

    resp = app.get(formdata.get_url(backoffice=True) + 'inspect')
    resp.form['test_mode'] = 'template'
    resp.form['template_loop'] = '{{ form_var_items }}'
    resp.form['template'] = 'idx:{{ status_loop.index }} / {{ status_loop.current_item }}'
    resp = resp.form.submit()
    assert resp.pyquery('.test-tool-result').length == 5
    assert (
        resp.pyquery('.test-tool-result-plain').text()
        == 'idx:1 / foo idx:2 / bar idx:3 / baz idx:4 / qux idx:5 / quux'
    )
    assert resp.pyquery('.infonotice:last-child').text() == 'Loop test limited to 5 iterations'

    resp.form['template_loop'] = '{% xxx %}'
    resp = resp.form.submit()
    assert resp.pyquery('.errornotice').text().count('Failed to evaluate loop template.') == 1

    resp.form['template_loop'] = '{{ 1 }}'
    resp = resp.form.submit()
    assert resp.pyquery('.errornotice').text() == 'Invalid value to be looped on (1)'

    resp.form['template_loop'] = '{{ form_var_items }}'
    resp.form['template'] = '{% xxx %}'
    resp = resp.form.submit()
    assert resp.pyquery('.errornotice').text().count('Failed to evaluate template.') == 1

    resp.form['test_mode'] = 'html_template'
    resp.form['html_template_loop'] = '{{ form_var_items }}'
    resp.form['html_template'] = 'idx:{{ status_loop.index }} / {{ status_loop.current_item }}'
    resp = resp.form.submit()
    assert resp.pyquery('.test-tool-result').length == 5
    assert (
        resp.pyquery('.test-tool-result-html').text()
        == 'idx:1 / foo idx:2 / bar idx:3 / baz idx:4 / qux idx:5 / quux'
    )

    formdata.data['1'] = []
    formdata.store()
    resp = resp.form.submit()
    assert resp.pyquery('.test-tool-result').text() == 'Loop template didn\'t provide any element.'


def test_inspect_page_wscall_error(pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall(name='xxx')
    wscall.description = 'description'
    wscall.request = {'url': 'http://remote.invalid/', 'method': 'GET'}
    wscall.record_on_errors = True
    wscall.notify_on_errors = True
    wscall.store()

    create_user(pub, is_admin=True)
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = login(get_app(pub)).get('%sinspect' % formdata.get_url(backoffice=True), status=200)

    resp.form['test_mode'] = 'template'
    resp.form['template'] = 'X{{ webservice.xxx|default:"hello" }}Y'

    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.invalid/', body=ConnectionError('...'))
        resp = resp.form.submit()
        assert resp.pyquery('.test-tool-result-plain').text() == 'XhelloY'
        assert not resp.pyquery('#test-tool-result .errornotice')
