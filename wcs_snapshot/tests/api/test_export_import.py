import io
import json
import os
import tarfile
import uuid
import xml.etree.ElementTree as ET

import pytest
from django.utils.timezone import localtime

from wcs import workflow_tests
from wcs.api_export_import import BundleDeclareJob, BundleImportJob, klass_to_slug
from wcs.applications import Application, ApplicationElement
from wcs.blocks import BlockDef
from wcs.carddata import ApplicationCardData
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
from wcs.fields import BlockField, CommentField, ComputedField, ItemField, PageField, StringField
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.afterjobs import AfterJob
from wcs.sql import Equal
from wcs.testdef import TestDef
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
from .utils import sign_uri


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[api-secrets]
coucou = 1234
'''
        )

    Application.wipe()
    ApplicationElement.wipe()
    Category.wipe()
    FormDef.wipe()
    CardDefCategory.wipe()
    CardDef.wipe()
    BlockCategory.wipe()
    BlockDef.wipe()
    WorkflowCategory.wipe()
    Workflow.wipe()
    MailTemplateCategory.wipe()
    MailTemplate.wipe()
    CommentTemplateCategory.wipe()
    CommentTemplate.wipe()
    DataSourceCategory.wipe()
    NamedDataSource.wipe()
    NamedWsCall.wipe()
    pub.custom_view_class.wipe()
    TestDef.wipe()
    pub.user_class.wipe()
    pub.test_user_class.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_export_import_index(pub):
    get_app(pub).get('/api/export-import/', status=403)

    resp = get_app(pub).get(sign_uri('/api/export-import/'))
    assert resp.json['data'] == [
        {
            'id': 'forms',
            'text': 'Forms',
            'singular': 'Form',
            'urls': {'list': 'http://example.net/api/export-import/forms/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'cards',
            'text': 'Cards',
            'singular': 'Card',
            'urls': {'list': 'http://example.net/api/export-import/cards/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'cards-data',
            'text': 'Cards data',
            'singular': 'Cards data',
            'minor': False,
            'urls': {'list': 'http://example.net/api/export-import/cards-data/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'If at least one card data already exists for this card on the instance where the application is deployed, '
                    'the cards data will not be imported.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'workflows',
            'text': 'Workflows',
            'singular': 'Workflow',
            'urls': {'list': 'http://example.net/api/export-import/workflows/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'blocks',
            'text': 'Blocks',
            'singular': 'Block of fields',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/blocks/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'data-sources',
            'text': 'Data Sources',
            'singular': 'Data Source',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/data-sources/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'mail-templates',
            'text': 'Mail Templates',
            'singular': 'Mail Template',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/mail-templates/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'comment-templates',
            'text': 'Comment Templates',
            'singular': 'Comment Template',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/comment-templates/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'wscalls',
            'text': 'Webservice Calls',
            'singular': 'Webservice Call',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/wscalls/'},
            'config_options': [
                {
                    'varname': 'install_only',
                    'field_type': 'bool',
                    'label': 'Installation only',
                    'help_text': 'This element will not be updated if it already exists on the instance where the application is deployed.',
                    'default_value': False,
                }
            ],
        },
        {
            'id': 'blocks-categories',
            'text': 'Categories (blocks)',
            'singular': 'Category (block)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/blocks-categories/'},
        },
        {
            'id': 'cards-categories',
            'text': 'Categories (cards)',
            'singular': 'Category (cards)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/cards-categories/'},
        },
        {
            'id': 'forms-categories',
            'text': 'Categories (forms)',
            'singular': 'Category (forms)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/forms-categories/'},
        },
        {
            'id': 'workflows-categories',
            'text': 'Categories (workflows)',
            'singular': 'Category (workflows)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/workflows-categories/'},
        },
        {
            'id': 'mail-templates-categories',
            'text': 'Categories (mail templates)',
            'singular': 'Category (mail templates)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/mail-templates-categories/'},
        },
        {
            'id': 'comment-templates-categories',
            'text': 'Categories (comment templates)',
            'singular': 'Category (comment templates)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/comment-templates-categories/'},
        },
        {
            'id': 'data-sources-categories',
            'text': 'Categories (data sources)',
            'singular': 'Category (data Sources)',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/data-sources-categories/'},
        },
        {
            'id': 'roles',
            'text': 'Roles',
            'singular': 'Role',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/roles/'},
        },
        {
            'id': 'users',
            'text': 'Test users',
            'singular': 'Test user',
            'minor': True,
            'urls': {'list': 'http://example.net/api/export-import/users/'},
        },
    ]


def test_export_import_list_forms(pub):
    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    assert not resp.json['data']

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    assert resp.json['data'][0]['id'] == 'test'
    assert resp.json['data'][0]['text'] == 'Test'
    assert resp.json['data'][0]['category'] is None

    category = Category(name='Test')
    category.store()
    formdef.category = category
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    assert resp.json['data'][0]['id'] == 'test'
    assert resp.json['data'][0]['text'] == 'Test'
    assert resp.json['data'][0]['category'] == 'Test'


def test_export_import_list_404(pub):
    get_app(pub).get(sign_uri('/api/export-import/xxx/'), status=404)


def test_export_import_form(pub):
    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['export']))
    assert resp.text.startswith('<formdef ')


def test_export_import_form_404(pub):
    get_app(pub).get(sign_uri('/api/export-import/xxx/plop/'), status=404)
    get_app(pub).get(sign_uri('/api/export-import/forms/plop/'), status=404)


def test_export_import_form_dependencies_404(pub):
    get_app(pub).get(sign_uri('/api/export-import/forms/plop/dependencies/'), status=404)


def test_export_import_dependencies(pub):
    role = pub.role_class(name='Test role')
    role.store()
    role2 = pub.role_class(name='Second role')
    role2.store()
    role3 = pub.role_class(name='Third role')
    role3.store()
    role4 = pub.role_class(name='Fourth role')
    role4.uuid = str(uuid.uuid4())
    role4.store()
    role5 = pub.role_class(name='Fifth role')
    role5.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()
    wscall = NamedWsCall(name='Test bis')
    wscall.store()
    wscall = NamedWsCall(name='Test ter')
    wscall.store()
    wscall = NamedWsCall(name='Test quater')
    wscall.store()
    wscall = NamedWsCall(name='Test quinquies')
    wscall.store()
    wscall = NamedWsCall(name='Test sexies')
    wscall.store()
    wscall = NamedWsCall(name='Test in computed field')
    wscall.store()
    wscall = NamedWsCall(name='Test in lateral template')
    wscall.store()
    wscall = NamedWsCall(name='Test in loop items template')
    wscall.store()
    wscall = NamedWsCall(name='Test in workflow form post condition')
    wscall.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test bis'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test ter'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test quater'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test in loop items template'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test in data source url'
    carddef.store()
    carddef = CardDef()
    carddef.name = 'Test in data source query string'
    carddef.store()

    formdef = FormDef()
    formdef.name = 'Test bis'
    formdef.store()
    formdef = FormDef()
    formdef.name = 'Test ter'
    formdef.store()
    formdef = FormDef()
    formdef.name = 'Test quater'
    formdef.store()
    formdef = FormDef()
    formdef.name = 'Test quinquies'
    formdef.store()
    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert not resp.json['data']

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared formdef custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {}
    custom_view.visibility = 'role'
    custom_view.role_id = role4.id
    custom_view.store()

    formdef.roles = ['logged-users']
    formdef.backoffice_submission_roles = [role2.id]
    formdef.workflow_roles = {'_receiver': role3.id}
    formdef.store()

    block = BlockDef(name='test')
    block.store()

    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        BlockField(id='bo1', label='test', block_slug='test'),
    ]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [StringField(label='Test', id='1')]

    status = workflow.add_status('New')
    status.loop_items_template = '{{ webservice.test_in_loop_items_template }}'
    action = status.add_action('form')
    action.by = [role.id]
    status = workflow.add_status('Next')
    status.loop_items_template = '{{ cards|objects:"test-in-loop-items-template" }}'

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': '{{ cards|objects:"test-in-data-source-url" }}'}
    data_source.qs_data = {'var1': '{{ cards|objects:"test-in-data-source-query-string" }}'}
    data_source.store()

    data_source2 = NamedDataSource(name='foobaz')
    data_source2.store()

    display_form = status.add_action('form', id='_x')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{ webservice.test_bis.plop }}'},
        )
    )
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{ webservice.unknown }}'},
        )
    )
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{ cards|objects:"unknown" }}'},
        )
    )
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{ cards|objects:"test-bis" }}'},
        )
    )
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={
                'type': 'string',
                'value': '{{ data_source.foobaz.plop }} {{ forms|objects:"test-bis" }}',
            },
        )
    )
    display_form.formdef.fields.append(
        StringField(
            label='Test',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{ forms|objects:"unknown" }}'},
        )
    )
    display_form.post_conditions = [
        {
            'condition': {'type': 'django', 'value': '{{ webservice.test_in_workflow_form_post_condition }}'},
            'error_message': 'foo',
        }
    ]

    send_mail = status.add_action('sendmail')
    send_mail.to = [role.id]
    send_mail.subject = '{% webservice "test" %}'
    send_mail.body = '{{ cards|objects:"test" }} {{ forms|objects:"test-ter" }}'
    send_mail.condition = {
        'type': 'django',
        'value': '{{ cards|objects:"test-ter" }} {{ webservice.test_ter }}',
    }

    register_comment = status.add_action('register-comment')
    register_comment.to = [role.id]
    register_comment.comment = (
        '{{ cards|objects:"test-quater" }} {{ forms|objects:"test-quinquies" }} {{ webservice.test_sexies }}'
    )

    dispatch_auto = status.add_action('dispatch')
    dispatch_auto.rules = [{'role_id': role.id, 'value': 'xxx'}]

    status.add_action('dispatch')  # unconfigured/manual dispatch

    workflow.store()

    formdef.fields = [
        PageField(
            id='0',
            label='Page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': '{{ cards|objects:"test-bis" }} {{ forms|objects:"test-quater" }} {{ webservice.test_quater }}',
                    },
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        BlockField(
            id='1',
            label='test',
            block_slug='test',
            condition={
                'type': 'django',
                'value': '{{ forms|objects:"test-bis" }} {{ webservice.test_quinquies }}',
            },
        ),
        BlockField(
            id='1bis',
            label='test_missing',
            block_slug='test-missing',  # Unknown BlockDef
        ),
        CommentField(
            id='2',
            label='X {% webservice "test" %} X {{ cards|objects:"test" }} X {{ forms|objects:"test-ter" }} X',
        ),
        ComputedField(
            id='3',
            label='computed field',
            varname='computed_field',
            value_template='{{ webservice.test_in_computed_field.xxx }}',
        ),
    ]
    formdef.workflow = workflow
    formdef.lateral_template = 'x{{ webservice.test_in_lateral_template.blah }}y'
    formdef.store()

    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role5.id]
    user.store()
    user2 = pub.user_class(name='test user 2')
    user2.test_uuid = '43'
    user2.store()
    user3 = pub.user_class(name='test user 3')
    user3.test_uuid = '44'
    user3.store()
    user4 = pub.user_class(name='test user 3')
    user4.test_uuid = '45'
    user4.store()

    carddef = CardDef()
    carddef.name = 'test dependency'
    carddef.store()

    dependency_testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    dependency_testdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.user_uuid = user.test_uuid
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Name', who='other', who_id=user2.test_uuid),
        workflow_tests.AssertUserCanView(user_uuid=user4.test_uuid),
    ]
    testdef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    form_data = [d for d in resp.json['data'] if d['id'] == 'test']
    resp = get_app(pub).get(sign_uri(form_data[0]['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('test', 'workflows'),
        ('test', 'blocks'),
        ('test', 'wscalls'),
        ('test_quater', 'wscalls'),
        ('test_quinquies', 'wscalls'),
        ('test_in_computed_field', 'wscalls'),
        ('test_in_lateral_template', 'wscalls'),
        ('test', 'cards'),
        ('test-bis', 'cards'),
        ('test-dependency', 'cards'),
        ('test-bis', 'forms'),
        ('test-ter', 'forms'),
        ('test-quater', 'forms'),
        ('second-role', 'roles'),
        ('third-role', 'roles'),
        ('fourth-role', 'roles'),
        ('fifth-role', 'roles'),
        ('42', 'users'),
        ('43', 'users'),
        ('45', 'users'),
    }
    for dependency in resp.json['data']:
        if dependency['type'] == 'roles':
            assert dependency['urls'] == {}
            continue
        get_app(pub).get(sign_uri(dependency['urls']['export']))
    roles = {x['id']: x for x in resp.json['data'] if x['type'] == 'roles'}
    assert roles['second-role']['uuid'] is None
    assert roles['third-role']['uuid'] is None
    assert roles['fourth-role']['uuid'] == role4.uuid

    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('foobar', 'data-sources'),
        ('foobaz', 'data-sources'),
        ('test', 'wscalls'),
        ('test_bis', 'wscalls'),
        ('test_ter', 'wscalls'),
        ('test_sexies', 'wscalls'),
        ('test_in_loop_items_template', 'wscalls'),
        ('test', 'cards'),
        ('test-bis', 'cards'),
        ('test-ter', 'cards'),
        ('test-quater', 'cards'),
        ('test-in-loop-items-template', 'cards'),
        ('test-ter', 'forms'),
        ('test-bis', 'forms'),
        ('test-quinquies', 'forms'),
        ('test', 'blocks'),
        ('test-role', 'roles'),
        ('test_in_workflow_form_post_condition', 'wscalls'),
    }
    for dependency in resp.json['data']:
        if dependency['type'] == 'roles':
            continue
        resp = get_app(pub).get(sign_uri(dependency['urls']['export']))
        if 'test-role' in dependency['urls']['export']:
            assert resp.json == {'name': 'Test role', 'slug': 'test-role', 'uuid': None}
            assert resp.content_type == 'application/json'
        else:
            assert resp.content_type == 'text/xml'

    mail_template = MailTemplate(name='test mail template')
    mail_template.store()
    send_mail.mail_template = mail_template.slug
    comment_template = CommentTemplate(name='test comment template')
    comment_template.store()
    register_comment.comment_template = comment_template.slug
    workflow.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('foobar', 'data-sources'),
        ('foobaz', 'data-sources'),
        ('test_bis', 'wscalls'),
        ('test_ter', 'wscalls'),
        ('test_in_loop_items_template', 'wscalls'),
        ('test-bis', 'cards'),
        ('test-ter', 'cards'),
        ('test-in-loop-items-template', 'cards'),
        ('test-bis', 'forms'),
        ('test', 'blocks'),
        ('test-mail-template', 'mail-templates'),
        ('test-comment-template', 'comment-templates'),
        ('test-role', 'roles'),
        ('test_in_workflow_form_post_condition', 'wscalls'),
    }
    for dependency in resp.json['data']:
        if dependency['type'] == 'roles':
            continue
        get_app(pub).get(sign_uri(dependency['urls']['export']))
    resp = get_app(pub).get(sign_uri(resp.json['data'][-3]['urls']['dependencies']))
    assert resp.json['data'] == []

    cat = MailTemplateCategory(name='Cat')
    cat.store()
    mail_template.category_id = cat.id
    mail_template.subject = '{% webservice "test" %}'
    mail_template.body = '{{ cards|objects:"test" }} {{ forms|objects:"test-ter" }}'
    mail_template.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    mail_template_entry = [x for x in resp.json['data'] if x['type'] == 'mail-templates'][0]
    resp = get_app(pub).get(sign_uri(mail_template_entry['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('cat', 'mail-templates-categories'),
        ('test', 'cards'),
        ('test', 'wscalls'),
        ('test-ter', 'forms'),
    }
    for dependency in resp.json['data']:
        get_app(pub).get(sign_uri(dependency['urls']['export']))

    cat = CommentTemplateCategory(name='Cat')
    cat.store()
    comment_template.category_id = cat.id
    comment_template.comment = (
        '{{ cards|objects:"test-quater" }} {{ forms|objects:"test-quinquies" }} {{ webservice.test_sexies }}'
    )
    comment_template.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    comment_template_entry = [x for x in resp.json['data'] if x['type'] == 'comment-templates'][0]
    resp = get_app(pub).get(sign_uri(comment_template_entry['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('cat', 'comment-templates-categories'),
        ('test-quater', 'cards'),
        ('test-quinquies', 'forms'),
        ('test_sexies', 'wscalls'),
    }
    for dependency in resp.json['data']:
        get_app(pub).get(sign_uri(dependency['urls']['export']))

    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    resp = get_app(pub).get(sign_uri(resp.json['data'][2]['urls']['dependencies']))
    assert resp.json['data'] == []

    cat = DataSourceCategory(name='Cat')
    cat.store()
    data_source.category_id = cat.id
    data_source.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/workflows/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    data_sources_entry = [
        x for x in resp.json['data'] if x['type'] == 'data-sources' and x['id'] == 'foobar'
    ][0]
    resp = get_app(pub).get(sign_uri(data_sources_entry['urls']['dependencies']))
    assert {(x['id'], x['type']) for x in resp.json['data']} == {
        ('cat', 'data-sources-categories'),
        ('test-in-data-source-url', 'cards'),
        ('test-in-data-source-query-string', 'cards'),
    }


def test_export_import_dependencies_default_workflow(pub):
    formdef = FormDef()
    formdef.name = 'Test'
    formdef.workflow_id = '_default'
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.workflow_id = '_carddef_default'
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert resp.json['data'] == []

    resp = get_app(pub).get(sign_uri('/api/export-import/cards/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert resp.json['data'] == []


def test_export_import_redirect_url(pub):
    workflow = Workflow(name='test')
    workflow.store()

    block = BlockDef(name='test')
    block.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()

    category = Category(name='Test')
    category.store()

    data_source = NamedDataSource(name='Test')
    data_source.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()

    mail_template = MailTemplate(name='Test')
    mail_template.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()

    comment_template = CommentTemplate(name='Test')
    comment_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()

    user = pub.user_class(name='test user')
    user.test_uuid = 'test'
    user.store()

    elements = [
        ('forms', '/backoffice/forms/%s/' % formdef.id),
        ('cards', '/backoffice/cards/%s/' % carddef.id),
        ('blocks', '/backoffice/forms/blocks/%s/' % block.id),
        ('workflows', '/backoffice/workflows/%s/' % workflow.id),
        ('forms-categories', '/backoffice/forms/categories/%s/' % category.id),
        ('data-sources', '/backoffice/settings/data-sources/%s/' % data_source.id),
        (
            'data-sources-categories',
            '/backoffice/forms/data-sources/categories/%s/' % ds_category.id,
        ),
        ('mail-templates', '/backoffice/workflows/mail-templates/%s/' % mail_template.id),
        (
            'mail-templates-categories',
            '/backoffice/workflows/mail-templates/categories/%s/' % mail_template_category.id,
        ),
        ('comment-templates', '/backoffice/workflows/comment-templates/%s/' % comment_template.id),
        (
            'comment-templates-categories',
            '/backoffice/workflows/comment-templates/categories/%s/' % comment_template_category.id,
        ),
        ('users', '/backoffice/forms/test-users/%s/' % user.id),
    ]
    for object_type, obj_url in elements:
        resp = get_app(pub).get(sign_uri('/api/export-import/%s/' % object_type))
        redirect_url = resp.json['data'][0]['urls']['redirect']
        assert redirect_url == 'http://example.net/api/export-import/%s/test/redirect/' % object_type
        resp = get_app(pub).get(redirect_url, status=302)
        assert resp.location == 'http://example.net%s' % obj_url
        get_app(pub).get('/api/export-import/%s/unknown/redirect/' % object_type, status=404)

        resp = get_app(pub).get(redirect_url + '?compare', status=302)
        assert resp.location == 'http://example.net%s' % obj_url

        resp = get_app(pub).get(
            redirect_url + '?compare&version1=bar&version2=bar&application=foo', status=302
        )
        assert (
            resp.location
            == 'http://example.net%shistory/compare?version1=bar&version2=bar&application=foo' % obj_url
        )

    role = pub.role_class(name='test')
    role.store()
    resp = get_app(pub).get(sign_uri('/api/export-import/roles/'))
    assert resp.json['data'][0]['urls'].get('redirect') is None
    get_app(pub).get('/api/export-import/roles/test/redirect/', status=404)


def create_bundle(elements, *args, **kwargs):
    visible = kwargs.get('visible', True)
    version_number = kwargs.get('version_number', '42.0')
    config_options = kwargs.get('config_options', {})
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'icon': 'foo.png',
            'description': 'Foo Bar',
            'documentation_url': 'http://foo.bar',
            'visible': visible,
            'version_number': version_number,
            'version_notes': 'foo bar blah',
            'elements': elements,
            'config_options': config_options,
        }
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)

        icon_fd = io.BytesIO(
            b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVQI12NoAAAAggCB3UNq9AAAAABJRU5ErkJggg=='
        )
        tarinfo = tarfile.TarInfo('foo.png')
        tarinfo.size = len(icon_fd.getvalue())
        tar.addfile(tarinfo, fileobj=icon_fd)

        for path, obj in args:
            tarinfo = tarfile.TarInfo(path)
            if hasattr(obj, 'export_for_application'):
                export, _ = obj.export_for_application()
                if isinstance(export, str):
                    export = export.encode()
            else:
                export = ET.tostring(obj.export_to_xml(include_id=True))
            tarinfo.size = len(export)
            tar.addfile(tarinfo, fileobj=io.BytesIO(export))

    return tar_io.getvalue()


def test_export_import_bundle_import(pub):
    pub.snapshot_class.wipe()
    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()

    workflow = Workflow(name='test')
    workflow.roles = {'_receiver': 'Receiver'}
    workflow.category = workflow_category
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='test', type='string'),
    ]
    workflow.store()

    block_category = BlockCategory(name='test')
    block_category.store()

    block = BlockDef(name='test')
    block.category = block_category
    block.store()

    role = pub.role_class(name='test')
    role.store()

    category = Category(name='Test')
    category.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.fields = [
        BlockField(id='1', label='test', block_slug='test'),
    ]
    formdef.workflow = workflow
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.disabled = False
    formdef.category = category
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared formdef custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    card_category = CardDefCategory(name='Test')
    card_category.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.category = card_category
    carddef.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    data_source = NamedDataSource(name='Test')
    data_source.category = ds_category
    data_source.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    mail_template = MailTemplate(name='Test')
    mail_template.category = mail_template_category
    mail_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    comment_template = CommentTemplate(name='Test')
    comment_template.category = comment_template_category
    comment_template.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()

    bundles = []
    for version_number in ['42.0', '42.1']:
        bundles.append(
            create_bundle(
                [
                    {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'forms', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks', 'slug': 'test', 'name': 'test'},
                    {'type': 'roles', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources', 'slug': 'test', 'name': 'test'},
                    {'type': 'wscalls', 'slug': 'test', 'name': 'test'},
                    {'type': 'users', 'slug': '42', 'name': '42'},
                    {'type': 'foobar', 'slug': 'test', 'name': 'test'},
                ],
                ('forms-categories/test', category),
                ('forms/test', formdef),
                ('cards-categories/test', card_category),
                ('cards/test', carddef),
                ('blocks-categories/test', block_category),
                ('blocks/test', block),
                ('workflows-categories/test', workflow_category),
                ('workflows/test', workflow),
                ('data-sources-categories/test', ds_category),
                ('data-sources/test', data_source),
                ('mail-templates-categories/test', mail_template_category),
                ('mail-templates/test', mail_template),
                ('comment-templates-categories/test', comment_template_category),
                ('comment-templates/test', comment_template),
                ('roles/test', role),
                ('wscalls/test', wscall),
                ('users/42', user),
                version_number=version_number,
            )
        )
    object_classes = [
        Category,
        FormDef,
        CardDefCategory,
        CardDef,
        BlockCategory,
        BlockDef,
        WorkflowCategory,
        Workflow,
        MailTemplateCategory,
        MailTemplate,
        CommentTemplateCategory,
        CommentTemplate,
        DataSourceCategory,
        NamedDataSource,
        pub.custom_view_class,
        pub.role_class,
        NamedWsCall,
        TestDef,
        pub.test_user_class,
    ]
    for object_class in object_classes:
        object_class.wipe()

    # roles will be created beforehand with authentic provisionning
    extra_role = pub.role_class(name='not this one')
    extra_role.store()
    role = pub.role_class(name='test')
    role.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[0])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '36/36 (100%)'

    assert Category.count() == 1
    assert FormDef.count() == 1
    assert FormDef.select()[0].fields[0].key == 'block'
    assert FormDef.select()[0].fields[0].block_slug == 'test'
    assert FormDef.select()[0].workflow_roles == {'_receiver': role.id}
    assert FormDef.select()[0].category_id == str(Category.select()[0].id)
    assert CardDefCategory.count() == 1
    assert CardDef.count() == 1
    assert CardDef.select()[0].category_id == str(CardDefCategory.select()[0].id)
    assert BlockCategory.count() == 1
    assert BlockDef.count() == 1
    assert BlockDef.select()[0].category_id == str(BlockCategory.select()[0].id)
    assert WorkflowCategory.count() == 1
    assert Workflow.count() == 1
    assert Workflow.select()[0].category_id == WorkflowCategory.select()[0].id
    assert MailTemplateCategory.count() == 1
    assert MailTemplate.count() == 1
    assert MailTemplate.select()[0].category_id == str(MailTemplateCategory.select()[0].id)
    assert CommentTemplateCategory.count() == 1
    assert CommentTemplate.count() == 1
    assert CommentTemplate.select()[0].category_id == str(CommentTemplateCategory.select()[0].id)
    assert DataSourceCategory.count() == 1
    assert NamedDataSource.count() == 1
    assert NamedDataSource.select()[0].category_id == str(DataSourceCategory.select()[0].id)
    assert NamedWsCall.count() == 1
    assert TestDef.count() == 1
    assert pub.test_user_class.count() == 1
    assert pub.custom_view_class().count() == 1
    assert Application.count() == 1
    application = Application.select()[0]
    assert application.slug == 'test'
    assert application.name == 'Test'
    assert application.description == 'Foo Bar'
    assert application.documentation_url == 'http://foo.bar'
    assert application.version_number == '42.0'
    assert application.version_notes == 'foo bar blah'
    assert application.icon.base_filename == 'foo.png'
    assert application.editable is False
    assert application.visible is True
    assert ApplicationElement.count() == 16

    # check backoffice field have been added to table
    # (a sql error would happen if it was missing)
    formdef = FormDef.select()[0]
    formdef.data_class().select()
    # check also that workflow's label in last snapshot does not starts with '[pre-import]'
    last_snapshot = pub.snapshot_class.get_latest(formdef.xml_root_node, formdef.id)
    assert last_snapshot.comment == 'Application (Test) complete initial installation'
    assert '<workflow slug="test" workflow_id="1">test</workflow>' in last_snapshot.get_serialization()

    for object_class in object_classes:
        if object_class in [pub.custom_view_class, pub.role_class, TestDef]:
            # no snapshot or not relevant for this objects
            continue
        for obj in object_class.select():
            last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
            if last_snapshot.comment == 'Application (Test) finalize initial installation':
                previous_snapshot = pub.snapshot_class.select_object_history(obj)[1]
                assert previous_snapshot.comment == 'Application (Test) complete initial installation'
                assert previous_snapshot.application_slug == 'test'
                assert previous_snapshot.application_version == '42.0'
                previous_snapshot = pub.snapshot_class.select_object_history(obj)[2]
                assert previous_snapshot.comment == 'Application (Test) initial installation'
                assert previous_snapshot.application_slug is None
                assert previous_snapshot.application_version is None
            elif last_snapshot.comment == 'Application (Test) complete initial installation':
                previous_snapshot = pub.snapshot_class.select_object_history(obj)[1]
                assert previous_snapshot.comment == 'Application (Test) initial installation'
                assert previous_snapshot.application_slug is None
                assert previous_snapshot.application_version is None
            else:
                assert last_snapshot.comment == 'Application (Test)'
            assert last_snapshot.application_slug == 'test'
            assert last_snapshot.application_version == '42.0'
    # check editable flag is kept on install
    application.editable = False
    application.store()

    # create some links to elements not present in manifest: they should be unlinked
    element1 = ApplicationElement()
    element1.application_id = application.id
    element1.object_type = 'foobar'
    element1.object_id = '42'
    element1.store()
    element2 = ApplicationElement()
    element2.application_id = application.id
    element2.object_type = 'foobarblah'
    element2.object_id = '35'
    element2.store()

    # run new import to check it doesn't duplicate objects
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[1])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    job = BundleImportJob.get(afterjob_url.split('/')[-2])
    assert job.timings[0]['duration']

    assert Category.count() == 1
    assert FormDef.count() == 1
    assert CardDefCategory.count() == 1
    assert CardDef.count() == 1
    assert BlockCategory.count() == 1
    assert BlockDef.count() == 1
    assert WorkflowCategory.count() == 1
    assert Workflow.count() == 1
    assert MailTemplateCategory.count() == 1
    assert MailTemplate.count() == 1
    assert CommentTemplateCategory.count() == 1
    assert CommentTemplate.count() == 1
    assert DataSourceCategory.count() == 1
    assert NamedDataSource.count() == 1
    assert pub.custom_view_class().count() == 1
    assert NamedWsCall.count() == 1
    assert TestDef.count() == 1
    assert pub.test_user_class.count() == 1
    assert Application.count() == 1
    assert ApplicationElement.count() == 16
    assert (
        ApplicationElement.select(
            [
                Equal('application_id', application.id),
                Equal('object_type', element1.object_type),
                Equal('object_id', element1.object_id),
            ]
        )
        == []
    )
    assert (
        ApplicationElement.select(
            [
                Equal('application_id', application.id),
                Equal('object_type', element2.object_type),
                Equal('object_id', element2.object_id),
            ]
        )
        == []
    )
    application = Application.select()[0]
    assert application.editable is False
    for object_class in object_classes:
        if object_class in [pub.custom_view_class, pub.role_class, TestDef]:
            # no snapshot or not relevant for this objects
            continue
        for obj in object_class.select():
            last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
            assert last_snapshot.comment == 'Application (Test) update'
            assert last_snapshot.application_slug == 'test'
            assert last_snapshot.application_version == '42.1'

    # change immutable attributes and check they are not reset
    formdef = FormDef.select()[0]
    formdef.workflow_roles = {'_receiver': extra_role.id}
    formdef.disabled = True
    formdef.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[1])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    formdef = FormDef.select()[0]
    assert formdef.disabled is True
    assert formdef.workflow_roles == {'_receiver': extra_role.id}

    # bad file format
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', b'garbage')]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = BundleImportJob.get(afterjob_url.split('/')[-2])
    assert job.status == 'failed'
    assert job.failure_label == 'Error: Invalid tar file.'

    # missing manifest
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        foo_fd = io.BytesIO(json.dumps({'foo': 'bar'}, indent=2).encode())
        tarinfo = tarfile.TarInfo('foo.json')
        tarinfo.size = len(foo_fd.getvalue())
        tar.addfile(tarinfo, fileobj=foo_fd)
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'),
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = BundleImportJob.get(afterjob_url.split('/')[-2])
    assert job.status == 'failed'
    assert job.failure_label == 'Error: Invalid tar file, missing manifest.'

    # missing component
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'elements': [{'type': 'forms', 'slug': 'foo', 'name': 'foo'}],
        }
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'),
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = BundleImportJob.get(afterjob_url.split('/')[-2])
    assert job.status == 'failed'
    assert job.failure_label == 'Error: Invalid tar file, missing component forms/foo.'


def test_export_import_bundle_import_install_only(pub):
    pub.snapshot_class.wipe()
    workflow_category = WorkflowCategory(name='Test')
    workflow_category.store()

    workflow = Workflow(name='Test')
    workflow.store()

    block_category = BlockCategory(name='Test')
    block_category.store()

    block = BlockDef(name='Test')
    block.documentation = 'test documentation'
    block.store()

    category = Category(name='Test')
    category.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.fields = [
        BlockField(id='1', label='Test', block_slug='test'),
    ]
    formdef.documentation = 'test documentation'
    formdef.workflow = workflow
    formdef.store()

    user = pub.user_class(name='Test')
    user.test_uuid = '42'
    user.store()

    card_category = CardDefCategory(name='Test')
    card_category.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [StringField(id='1', label='Test')]
    carddef.documentation = 'test documentation'
    carddef.store()
    for i in range(10):
        carddata = carddef.data_class()()
        carddata.data = {'1': 'data %s' % i}
        carddata.just_created()
        carddata.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    data_source = NamedDataSource(name='Test')
    data_source.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    mail_template = MailTemplate(name='Test')
    mail_template.category = mail_template_category
    mail_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    comment_template = CommentTemplate(name='Test')
    comment_template.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()

    bundles = []
    for version_number, install_only in [('42.0', True), ('42.1', True), ('43.0', False), ('43.1', None)]:
        bundles.append(
            create_bundle(
                [
                    {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'forms', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-data', 'slug': 'test', 'name': 'Test'},
                    {'type': 'blocks-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources', 'slug': 'test', 'name': 'test'},
                    {'type': 'wscalls', 'slug': 'test', 'name': 'test'},
                    {'type': 'users', 'slug': '42', 'name': '42'},
                    {'type': 'foobar', 'slug': 'test', 'name': 'test'},
                ],
                ('forms-categories/test', category),
                ('forms/test', formdef),
                ('cards-categories/test', card_category),
                ('cards/test', carddef),
                ('cards-data/test', ApplicationCardData(carddef)),
                ('blocks-categories/test', block_category),
                ('blocks/test', block),
                ('workflows-categories/test', workflow_category),
                ('workflows/test', workflow),
                ('data-sources-categories/test', ds_category),
                ('data-sources/test', data_source),
                ('mail-templates-categories/test', mail_template_category),
                ('mail-templates/test', mail_template),
                ('comment-templates-categories/test', comment_template_category),
                ('comment-templates/test', comment_template),
                ('wscalls/test', wscall),
                ('users/42', user),
                version_number=version_number,
                config_options=(
                    {
                        # invalid options are ignored
                        'install_only': {
                            'forms-categories/test': install_only,
                            'forms/test': install_only,
                            'cards-categories/test': install_only,
                            'cards/test': install_only,
                            'cards-data/test': install_only,
                            'blocks-categories/test': install_only,
                            'blocks/test': install_only,
                            'workflows-categories/test': install_only,
                            'workflows/test': install_only,
                            'data-sources-categories/test': install_only,
                            'data-sources/test': install_only,
                            'mail-templates-categories/test': install_only,
                            'mail-templates/test': install_only,
                            'comment-templates-categories/test': install_only,
                            'comment-templates/test': install_only,
                            'wscalls/test': install_only,
                            'users/42': install_only,
                        }
                    }
                    if install_only is not None
                    else {}
                ),
            )
        )
    carddef.data_class().wipe()
    object_classes = [
        Category,
        FormDef,
        CardDefCategory,
        CardDef,
        BlockCategory,
        BlockDef,
        WorkflowCategory,
        Workflow,
        MailTemplateCategory,
        MailTemplate,
        CommentTemplateCategory,
        CommentTemplate,
        DataSourceCategory,
        NamedDataSource,
        NamedWsCall,
        pub.test_user_class,
    ]
    object_classes_with_install_only_option = [
        FormDef,
        CardDef,
        BlockDef,
        Workflow,
        MailTemplate,
        CommentTemplate,
        NamedDataSource,
        NamedWsCall,
    ]
    for object_class in object_classes:
        object_class.wipe()

    # first bundle, version 42.0, install only for all elements
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[0])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'

    assert Application.count() == 1
    application = Application.select()[0]
    assert application.version_number == '42.0'
    for object_class in object_classes:
        assert object_class.count() == 1
        obj = object_class.select()[0]
        assert obj.name == 'Test'
        last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
        assert last_snapshot.application_slug == 'test'
        assert last_snapshot.application_version == '42.0'
    assert {x.data['1'] for x in CardDef.get_by_slug('test').data_class().select()} == {
        'data %s' % x for x in range(10)
    }

    def alter_local_data():
        for object_class in object_classes:
            obj = object_class.select()[0]
            if object_class in (CardDef, FormDef, BlockDef):
                # block/formdef/carddef names are kept on updates, so change a different attribute.
                obj.documentation = 'local changes'
            else:
                obj.name = 'local-changes'
            obj.store()
        for i, carddata in enumerate(CardDef.get_by_slug('test').data_class().select()):
            if not i:
                continue
            carddata.remove_self()

    alter_local_data()

    # second bundle, version 42.1, install only for all elements
    # elements with option install only available already exist, they are not updated
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[1])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'

    assert Application.count() == 1
    application = Application.select()[0]
    assert application.version_number == '42.1'
    for object_class in object_classes:
        assert object_class.count() == 1
        obj = object_class.select()[0]
        if object_class in object_classes_with_install_only_option:
            if object_class in (CardDef, FormDef, BlockDef):
                assert obj.documentation == 'local changes'
            else:
                assert obj.name == 'local-changes'
        else:
            assert obj.name == 'Test'
        last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
        if object_class in object_classes_with_install_only_option:
            assert last_snapshot.application_slug is None
            assert last_snapshot.application_version is None
        else:
            assert last_snapshot.application_slug == 'test'
            assert last_snapshot.application_version == '42.1'
    assert {x.data['1'] for x in CardDef.get_by_slug('test').data_class().select()} == {'data 0'}

    alter_local_data()

    # third bundle, version 43.0, install only is False, all elements are updated
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[2])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'

    assert Application.count() == 1
    application = Application.select()[0]
    assert application.version_number == '43.0'
    for object_class in object_classes:
        assert object_class.count() == 1
        obj = object_class.select()[0]
        if object_class in (CardDef, FormDef, BlockDef):
            assert obj.documentation == 'test documentation'
        else:
            assert obj.name == 'Test'
        last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
        assert last_snapshot.application_slug == 'test'
        assert last_snapshot.application_version == '43.0'
    assert {x.data['1'] for x in CardDef.get_by_slug('test').data_class().select()} == {
        'data %s' % x for x in range(10)
    }

    alter_local_data()

    # fourth bundle, version 43.1, install only is missing (default is False), all elements are updated
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[3])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'

    assert Application.count() == 1
    application = Application.select()[0]
    assert application.version_number == '43.1'
    for object_class in object_classes:
        assert object_class.count() == 1
        obj = object_class.select()[0]
        if object_class in (CardDef, FormDef, BlockDef):
            assert obj.documentation == 'test documentation'
        else:
            assert obj.name == 'Test'
        last_snapshot = pub.snapshot_class.select_object_history(obj)[0]
        assert last_snapshot.application_slug == 'test'
        assert last_snapshot.application_version == '43.1'
    assert {x.data['1'] for x in CardDef.get_by_slug('test').data_class().select()} == {
        'data %s' % x for x in range(10)
    }


@pytest.mark.parametrize(
    'category_class',
    [
        Category,
        CardDefCategory,
        BlockCategory,
        WorkflowCategory,
        MailTemplateCategory,
        CommentTemplateCategory,
        DataSourceCategory,
    ],
)
def test_export_import_bundle_import_categories_ordering(pub, category_class):
    pub.snapshot_class.wipe()
    category_class.wipe()
    category1 = category_class(name='cat 1')
    category1.position = 1
    category1.store()
    category2 = category_class(name='cat 2')
    category2.position = 2
    category2.store()
    category3 = category_class(name='cat 3')
    category3.position = 3
    category3.store()
    bundle = create_bundle(
        [
            {'type': klass_to_slug[category_class], 'slug': 'cat-1', 'name': 'cat 1'},
            {'type': klass_to_slug[category_class], 'slug': 'cat-2', 'name': 'cat 2'},
            {'type': klass_to_slug[category_class], 'slug': 'cat-3', 'name': 'cat 3'},
        ],
        ('%s/cat-1' % klass_to_slug[category_class], category_class.get(category1.id)),
        ('%s/cat-2' % klass_to_slug[category_class], category_class.get(category2.id)),
        ('%s/cat-3' % klass_to_slug[category_class], category_class.get(category3.id)),
    )

    # delete categories
    category_class.wipe()
    # and recreate only cat 4 and 5 in first positions
    category4 = category_class(name='cat 4')
    category4.position = 1
    category4.store()
    category5 = category_class(name='cat 5')
    category5.position = 2
    category5.store()

    # import bundle
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed at the end
    assert category_class.get_by_slug('cat-4').position == 1
    assert category_class.get_by_slug('cat-5').position == 2
    assert category_class.get_by_slug('cat-1').position == 3
    assert category_class.get_by_slug('cat-2').position == 4
    assert category_class.get_by_slug('cat-3').position == 5

    # delete categories
    category_class.wipe()
    # recreate only cat 2, cat 4, cat 5 in this order
    category2 = category_class(name='cat 2')
    category2.position = 1
    category2.store()
    category4 = category_class(name='cat 4')
    category4.position = 2
    category4.store()
    category5 = category_class(name='cat 5')
    category5.position = 3
    category5.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed after cat 4
    assert category_class.get_by_slug('cat-1').position == 1
    assert category_class.get_by_slug('cat-2').position == 2
    assert category_class.get_by_slug('cat-3').position == 3
    assert category_class.get_by_slug('cat-4').position == 4
    assert category_class.get_by_slug('cat-5').position == 5

    # delete categories
    category_class.wipe()
    # recreate only cat 4, cat 2, cat 5 in this order
    category4 = category_class(name='cat 4')
    category4.position = 1
    category4.store()
    category2 = category_class(name='cat 2')
    category2.position = 2
    category2.store()
    category5 = category_class(name='cat 5')
    category5.position = 3
    category5.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed after cat 4
    assert category_class.get_by_slug('cat-4').position == 1
    assert category_class.get_by_slug('cat-1').position == 2
    assert category_class.get_by_slug('cat-2').position == 3
    assert category_class.get_by_slug('cat-3').position == 4
    assert category_class.get_by_slug('cat-5').position == 5

    # delete categories
    category_class.wipe()
    # recreate only cat 4, cat 5, cat 2 in this order
    category4 = category_class(name='cat 4')
    category4.position = 1
    category4.store()
    category5 = category_class(name='cat 5')
    category5.position = 2
    category5.store()
    category2 = category_class(name='cat 2')
    category2.position = 3
    category2.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed after cat 4
    assert category_class.get_by_slug('cat-4').position == 1
    assert category_class.get_by_slug('cat-5').position == 2
    assert category_class.get_by_slug('cat-1').position == 3
    assert category_class.get_by_slug('cat-2').position == 4
    assert category_class.get_by_slug('cat-3').position == 5

    # delete categories
    category_class.wipe()
    # recreate only cat 4, cat 2, cat1 cat 5 in this order but with weird positions
    category4 = category_class(name='cat 4')
    category4.position = 4
    category4.store()
    category2 = category_class(name='cat 2')
    category2.position = 12
    category2.store()
    category1 = category_class(name='cat 1')
    category1.position = 13
    category1.store()
    category5 = category_class(name='cat 5')
    category5.position = 20
    category5.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed after cat 4
    assert category_class.get_by_slug('cat-4').position == 1
    assert category_class.get_by_slug('cat-1').position == 2
    assert category_class.get_by_slug('cat-2').position == 3
    assert category_class.get_by_slug('cat-3').position == 4
    assert category_class.get_by_slug('cat-5').position == 5

    # delete categories
    category_class.wipe()
    # recreate only cat 4, cat 2, cat1 cat 5 in this order but with weird positions
    category4 = category_class(name='cat 4')
    category4.position = 1
    category4.store()
    category2 = category_class(name='cat 2')
    category2.position = 2
    category2.store()
    category1 = category_class(name='cat 1')
    category1.position = 2
    category1.store()
    category5 = category_class(name='cat 5')
    category5.position = None  # no position
    category5.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # cat 1, 2, 3 are placed after cat 4
    assert category_class.get_by_slug('cat-4').position == 1
    assert category_class.get_by_slug('cat-1').position == 2
    assert category_class.get_by_slug('cat-2').position == 3
    assert category_class.get_by_slug('cat-3').position == 4
    assert category_class.get_by_slug('cat-5').position == 5


def test_export_import_formdef_do_not_overwrite_table_name(pub):
    pub.snapshot_class.wipe()
    formdef = FormDef()
    formdef.name = 'Test2'
    formdef.fields = []
    formdef.disabled = False
    formdef.store()

    assert formdef.table_name == 'formdata_%s_test2' % formdef.id

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    # change formdef url name, internal table name won't be changed
    formdef.url_name = 'test'
    formdef.store()
    assert formdef.table_name == 'formdata_%s_test2' % formdef.id
    assert formdef.data_class().count() == 1

    bundle = create_bundle([{'type': 'forms', 'slug': 'test', 'name': 'test'}], ('forms/test', formdef))

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # check table name is not overwritten
    formdef = FormDef.select()[0]
    assert formdef.table_name == 'formdata_%s_test2' % formdef.id
    assert formdef.data_class().count() == 1


def test_export_import_bundle_declare(pub):
    pub.snapshot_class.wipe()
    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()

    workflow = Workflow(name='test')
    workflow.store()

    block_category = BlockCategory(name='test')
    block_category.store()

    block = BlockDef(name='test')
    block.store()

    role = pub.role_class(name='test')
    role.store()

    category = Category(name='Test')
    category.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    card_category = CardDefCategory(name='Test')
    card_category.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    data_source = NamedDataSource(name='Test')
    data_source.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    mail_template = MailTemplate(name='Test')
    mail_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    comment_template = CommentTemplate(name='Test')
    comment_template.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()

    bundle = create_bundle(
        [
            {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'forms', 'slug': 'test', 'name': 'test'},
            {'type': 'cards-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'cards', 'slug': 'test', 'name': 'test'},
            {'type': 'blocks-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'blocks', 'slug': 'test', 'name': 'test'},
            {'type': 'roles', 'slug': 'test', 'name': 'test'},
            {'type': 'workflows-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
            {'type': 'mail-templates-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'mail-templates', 'slug': 'test', 'name': 'test'},
            {'type': 'comment-templates-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'comment-templates', 'slug': 'test', 'name': 'test'},
            {'type': 'data-sources-categories', 'slug': 'test', 'name': 'test'},
            {'type': 'data-sources', 'slug': 'test', 'name': 'test'},
            {'type': 'data-sources', 'slug': 'unknown', 'name': 'unknown'},
            {'type': 'wscalls', 'slug': 'test', 'name': 'test'},
            {'type': 'foobar', 'slug': 'test', 'name': 'test'},
        ],
        ('forms-categories/test', category),
        ('forms/test', formdef),
        ('cards-categories/test', card_category),
        ('cards/test', carddef),
        ('blocks-categories/test', block_category),
        ('blocks/test', block),
        ('workflows-categories/test', workflow_category),
        ('workflows/test', workflow),
        ('data-sources-categories/test', ds_category),
        ('data-sources/test', data_source),
        ('data-sources/unknown', data_source),
        ('mail-templates-categories/test', mail_template_category),
        ('mail-templates/test', mail_template),
        ('comment-templates-categories/test', comment_template_category),
        ('comment-templates/test', comment_template),
        ('roles/test', role),
        ('wscalls/test', wscall),
        visible=False,
    )

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-declare/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '16/16 (100%)'

    assert Application.count() == 1
    application = Application.select()[0]
    assert application.slug == 'test'
    assert application.name == 'Test'
    assert application.description == 'Foo Bar'
    assert application.documentation_url == 'http://foo.bar'
    assert application.version_number == '42.0'
    assert application.version_notes == 'foo bar blah'
    assert application.icon.base_filename == 'foo.png'
    assert application.editable is True
    assert application.visible is False
    assert ApplicationElement.count() == 15

    # create some links to elements not present in manifest: they should be unlinked
    element1 = ApplicationElement()
    element1.application_id = application.id
    element1.object_type = 'foobar'
    element1.object_id = '42'
    element1.store()
    element2 = ApplicationElement()
    element2.application_id = application.id
    element2.object_type = 'foobarblah'
    element2.object_id = '35'
    element2.store()
    # and remove an object to have an unkown reference in manifest
    MailTemplate.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-declare/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    assert Application.count() == 1
    assert ApplicationElement.count() == 14
    assert (
        ApplicationElement.select(
            [
                Equal('application_id', application.id),
                Equal('object_type', element1.object_type),
                Equal('object_id', element1.object_id),
            ]
        )
        == []
    )
    assert (
        ApplicationElement.select(
            [
                Equal('application_id', application.id),
                Equal('object_type', element2.object_type),
                Equal('object_id', element2.object_id),
            ]
        )
        == []
    )
    assert (
        ApplicationElement.select(
            [
                Equal('application_id', application.id),
                Equal('object_type', MailTemplate.xml_root_node),
            ]
        )
        == []
    )

    # bad file format
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-declare/'), upload_files=[('bundle', 'bundle.tar', b'garbage')]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = BundleDeclareJob.get(afterjob_url.split('/')[-2])
    assert job.status == 'failed'
    assert job.failure_label == 'Error: Invalid tar file.'

    # missing manifest
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        foo_fd = io.BytesIO(json.dumps({'foo': 'bar'}, indent=2).encode())
        tarinfo = tarfile.TarInfo('foo.json')
        tarinfo.size = len(foo_fd.getvalue())
        tar.addfile(tarinfo, fileobj=foo_fd)
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-declare/'),
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = BundleDeclareJob.get(afterjob_url.split('/')[-2])
    assert job.status == 'failed'
    assert job.failure_label == 'Error: Invalid tar file, missing manifest.'


def test_export_import_bundle_unlink(pub):
    pub.snapshot_class.wipe()
    application = Application()
    application.slug = 'test'
    application.name = 'Test'
    application.version_number = 'foo'
    application.store()

    other_application = Application()
    other_application.slug = 'other-test'
    other_application.name = 'Other Test'
    other_application.version_number = 'foo'
    other_application.store()

    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()
    ApplicationElement.update_or_create_for_object(application, workflow_category)

    workflow = Workflow(name='test')
    workflow.store()
    ApplicationElement.update_or_create_for_object(application, workflow)

    block_category = BlockCategory(name='test')
    block_category.store()
    ApplicationElement.update_or_create_for_object(application, block_category)

    block = BlockDef(name='test')
    block.store()
    ApplicationElement.update_or_create_for_object(application, block)

    category = Category(name='Test')
    category.store()
    ApplicationElement.update_or_create_for_object(application, category)

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()
    ApplicationElement.update_or_create_for_object(application, formdef)

    card_category = CardDefCategory(name='Test')
    card_category.store()
    ApplicationElement.update_or_create_for_object(application, card_category)

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()
    ApplicationElement.update_or_create_for_object(application, carddef)

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    ApplicationElement.update_or_create_for_object(application, ds_category)
    data_source = NamedDataSource(name='Test')
    data_source.store()
    ApplicationElement.update_or_create_for_object(application, data_source)

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    ApplicationElement.update_or_create_for_object(application, mail_template_category)
    mail_template = MailTemplate(name='Test')
    mail_template.store()
    ApplicationElement.update_or_create_for_object(application, mail_template)

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    ApplicationElement.update_or_create_for_object(application, comment_template_category)
    comment_template = CommentTemplate(name='Test')
    comment_template.store()
    ApplicationElement.update_or_create_for_object(application, comment_template)

    wscall = NamedWsCall(name='Test')
    wscall.store()
    ApplicationElement.update_or_create_for_object(application, wscall)

    element = ApplicationElement()
    element.application_id = application.id
    element.object_type = 'foobar'
    element.object_id = '42'
    element.store()

    other_element = ApplicationElement()
    other_element.application_id = other_application.id
    other_element.object_type = 'foobar'
    other_element.object_id = '42'
    other_element.store()

    assert Application.count() == 2
    assert ApplicationElement.count() == 17

    get_app(pub).post(sign_uri('/api/export-import/unlink/'), {'application': 'test'})

    assert Application.count() == 1
    assert ApplicationElement.count() == 1

    assert (
        Application.count(
            [
                Equal('id', other_application.id),
            ]
        )
        == 1
    )
    assert (
        ApplicationElement.count(
            [
                Equal('application_id', other_application.id),
            ]
        )
        == 1
    )

    # again
    get_app(pub).post(sign_uri('/api/export-import/unlink/'), {'application': 'test'})
    assert Application.count() == 1
    assert ApplicationElement.count() == 1


def test_export_import_bundle_check(pub):
    pub.snapshot_class.wipe()
    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()

    workflow = Workflow(name='test')
    workflow.store()

    block_category = BlockCategory(name='test')
    block_category.store()

    block = BlockDef(name='test')
    block.store()

    role = pub.role_class(name='test')
    role.store()

    category = Category(name='Test')
    category.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    card_category = CardDefCategory(name='Test')
    card_category.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    data_source = NamedDataSource(name='Test')
    data_source.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    mail_template = MailTemplate(name='Test')
    mail_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    comment_template = CommentTemplate(name='Test')
    comment_template.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()

    user = pub.user_class(name='Test')
    user.test_uuid = 'test'
    user.store()

    bundles = []
    for version in ['1.0', '2.0']:
        bundles.append(
            create_bundle(
                [
                    {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'forms', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-data', 'slug': 'test', 'name': 'Test'},
                    {'type': 'blocks-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks', 'slug': 'test', 'name': 'test'},
                    {'type': 'roles', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources', 'slug': 'test', 'name': 'test'},
                    {'type': 'wscalls', 'slug': 'test', 'name': 'test'},
                    {'type': 'users', 'slug': 'test', 'name': 'test'},
                    {'type': 'foobar', 'slug': 'test', 'name': 'test'},
                ],
                ('forms-categories/test', category),
                ('forms/test', formdef),
                ('cards-categories/test', card_category),
                ('cards/test', carddef),
                ('cards-data/test', ApplicationCardData(carddef)),
                ('blocks-categories/test', block_category),
                ('blocks/test', block),
                ('workflows-categories/test', workflow_category),
                ('workflows/test', workflow),
                ('data-sources-categories/test', ds_category),
                ('data-sources/test', data_source),
                ('mail-templates-categories/test', mail_template_category),
                ('mail-templates/test', mail_template),
                ('comment-templates-categories/test', comment_template_category),
                ('comment-templates/test', comment_template),
                ('roles/test', role),
                ('wscalls/test', wscall),
                ('users/test', user),
                visible=False,
                version=version,
            )
        )

    elements_from_next_bundle = json.dumps(
        [
            'forms-categories/test',
            'forms/test',
            'cards-categories/test',
            'cards/test',
            'cards-data/test',
            'blocks-categories/test',
            'blocks/test',
            'workflows-categories/test',
            'workflows/test',
            'data-sources-categories/test',
            'data-sources/test',
            'mail-templates-categories/test',
            'mail-templates/test',
            'comment-templates-categories/test',
            'comment-templates/test',
            'roles/test',
            'wscalls/test',
            'users/test',
        ]
    )

    object_classes = [
        Category,
        FormDef,
        CardDefCategory,
        CardDef,
        BlockCategory,
        BlockDef,
        WorkflowCategory,
        Workflow,
        MailTemplateCategory,
        MailTemplate,
        CommentTemplateCategory,
        CommentTemplate,
        DataSourceCategory,
        NamedDataSource,
        pub.custom_view_class,
        pub.role_class,
        NamedWsCall,
        pub.test_user_class,
    ]
    for object_class in object_classes:
        object_class.wipe()
    pub.snapshot_class.wipe()

    incomplete_bundles = []
    for manifest_json in [{'slug': 'test'}, {'version_number': '1.0'}]:
        tar_io = io.BytesIO()
        with tarfile.open(mode='w', fileobj=tar_io) as tar:
            manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
            tarinfo = tarfile.TarInfo('manifest.json')
            tarinfo.size = len(manifest_fd.getvalue())
            tar.addfile(tarinfo, fileobj=manifest_fd)
        incomplete_bundles.append(tar_io.getvalue())

    # incorrect bundles, missing information
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        upload_files=[('bundle', 'bundle.tar', incomplete_bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.get(afterjob_url.split('/')[-2])
    assert job.failure_label == 'Error: Invalid tar file, missing version.'
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        upload_files=[('bundle', 'bundle.tar', incomplete_bundles[1])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.get(afterjob_url.split('/')[-2])
    assert job.failure_label == 'Error: Invalid tar file, missing application.'

    # not yet imported
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'no_history_elements': [],
        'unknown_elements': [
            {'slug': 'test', 'type': 'forms-categories'},
            {'slug': 'test', 'type': 'forms'},
            {'slug': 'test', 'type': 'cards-categories'},
            {'slug': 'test', 'type': 'cards'},
            {'slug': 'test', 'type': 'blocks-categories'},
            {'slug': 'test', 'type': 'blocks'},
            {'slug': 'test', 'type': 'workflows-categories'},
            {'slug': 'test', 'type': 'workflows'},
            {'slug': 'test', 'type': 'mail-templates-categories'},
            {'slug': 'test', 'type': 'mail-templates'},
            {'slug': 'test', 'type': 'comment-templates-categories'},
            {'slug': 'test', 'type': 'comment-templates'},
            {'slug': 'test', 'type': 'data-sources-categories'},
            {'slug': 'test', 'type': 'data-sources'},
            {'slug': 'test', 'type': 'wscalls'},
            {'slug': 'test', 'type': 'users'},
        ],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # import bundle
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[0])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'
    assert Application.count() == 1
    assert ApplicationElement.count() == 16

    # remove application links
    Application.wipe()
    ApplicationElement.wipe()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'forms-categories',
                'url': 'http://example.net/api/export-import/forms-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'forms',
                'url': 'http://example.net/api/export-import/forms/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'cards-categories',
                'url': 'http://example.net/api/export-import/cards-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'cards',
                'url': 'http://example.net/api/export-import/cards/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'blocks-categories',
                'url': 'http://example.net/api/export-import/blocks-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'blocks',
                'url': 'http://example.net/api/export-import/blocks/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'workflows-categories',
                'url': 'http://example.net/api/export-import/workflows-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'workflows',
                'url': 'http://example.net/api/export-import/workflows/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'mail-templates-categories',
                'url': 'http://example.net/api/export-import/mail-templates-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'mail-templates',
                'url': 'http://example.net/api/export-import/mail-templates/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'comment-templates-categories',
                'url': 'http://example.net/api/export-import/comment-templates-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'comment-templates',
                'url': 'http://example.net/api/export-import/comment-templates/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'data-sources-categories',
                'url': 'http://example.net/api/export-import/data-sources-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'data-sources',
                'url': 'http://example.net/api/export-import/data-sources/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'wscalls',
                'url': 'http://example.net/api/export-import/wscalls/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'users',
                'url': 'http://example.net/api/export-import/users/test/redirect/',
            },
        ],
        'uninstalled_elements': [],
    }

    # import bundle again, recreate links
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[0])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'
    assert Application.count() == 1
    assert ApplicationElement.count() == 16

    # no changes since last import
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # add local changes
    snapshots = {}
    for object_class in object_classes:
        if object_class in [pub.custom_view_class, pub.role_class]:
            # no snapshot for this objects
            continue
        for obj in object_class.select():
            old_snapshots = pub.snapshot_class.select_object_history(obj)
            obj.name = 'new name !'
            obj.store(comment='local change !')
            new_snapshots = pub.snapshot_class.select_object_history(obj)
            snapshots['%s:%s' % (object_class.xml_root_node, obj.slug)] = (
                old_snapshots[0].id,
                new_snapshots[0].id,
            )
            assert len(new_snapshots) > len(old_snapshots)

    # and check
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [
            {
                'slug': 'test',
                'type': 'forms-categories',
                'url': 'http://example.net/backoffice/forms/categories/%s/history/compare?version1=%s&version2=%s'
                % (Category.get_by_slug('test').id, *snapshots['category:test']),
            },
            {
                'slug': 'test',
                'type': 'forms',
                'url': 'http://example.net/backoffice/forms/1/history/compare?version1=%s&version2=%s'
                % snapshots['formdef:test'],
            },
            {
                'slug': 'test',
                'type': 'cards-categories',
                'url': 'http://example.net/backoffice/cards/categories/%s/history/compare?version1=%s&version2=%s'
                % (CardDefCategory.get_by_slug('test').id, *snapshots['carddef_category:test']),
            },
            {
                'slug': 'test',
                'type': 'cards',
                'url': 'http://example.net/backoffice/cards/1/history/compare?version1=%s&version2=%s'
                % snapshots['carddef:test'],
            },
            {
                'slug': 'test',
                'type': 'blocks-categories',
                'url': 'http://example.net/backoffice/forms/blocks/categories/%s/history/compare?version1=%s&version2=%s'
                % (BlockCategory.get_by_slug('test').id, *snapshots['block_category:test']),
            },
            {
                'slug': 'test',
                'type': 'blocks',
                'url': 'http://example.net/backoffice/forms/blocks/1/history/compare?version1=%s&version2=%s'
                % snapshots['block:test'],
            },
            {
                'slug': 'test',
                'type': 'workflows-categories',
                'url': 'http://example.net/backoffice/workflows/categories/%s/history/compare?version1=%s&version2=%s'
                % (WorkflowCategory.get_by_slug('test').id, *snapshots['workflow_category:test']),
            },
            {
                'slug': 'test',
                'type': 'workflows',
                'url': 'http://example.net/backoffice/workflows/1/history/compare?version1=%s&version2=%s'
                % snapshots['workflow:test'],
            },
            {
                'slug': 'test',
                'type': 'mail-templates-categories',
                'url': 'http://example.net/backoffice/workflows/mail-templates/categories/%s/history/compare?version1=%s&version2=%s'
                % (MailTemplateCategory.get_by_slug('test').id, *snapshots['mail_template_category:test']),
            },
            {
                'slug': 'test',
                'type': 'mail-templates',
                'url': 'http://example.net/backoffice/workflows/mail-templates/1/history/compare?version1=%s&version2=%s'
                % snapshots['mail-template:test'],
            },
            {
                'slug': 'test',
                'type': 'comment-templates-categories',
                'url': 'http://example.net/backoffice/workflows/comment-templates/categories/%s/history/compare?version1=%s&version2=%s'
                % (
                    CommentTemplateCategory.get_by_slug('test').id,
                    *snapshots['comment_template_category:test'],
                ),
            },
            {
                'slug': 'test',
                'type': 'comment-templates',
                'url': 'http://example.net/backoffice/workflows/comment-templates/1/history/compare?version1=%s&version2=%s'
                % snapshots['comment-template:test'],
            },
            {
                'slug': 'test',
                'type': 'data-sources-categories',
                'url': 'http://example.net/backoffice/forms/data-sources/categories/%s/history/compare?version1=%s&version2=%s'
                % (DataSourceCategory.get_by_slug('test').id, *snapshots['data_source_category:test']),
            },
            {
                'slug': 'test',
                'type': 'data-sources',
                'url': 'http://example.net/backoffice/settings/data-sources/1/history/compare?version1=%s&version2=%s'
                % snapshots['datasource:test'],
            },
            {
                'slug': 'test',
                'type': 'wscalls',
                'url': 'http://example.net/backoffice/settings/wscalls/1/history/compare?version1=%s&version2=%s'
                % snapshots['wscall:test'],
            },
            {
                'slug': 'test',
                'type': 'users',
                'url': 'http://example.net/backoffice/forms/test-users/%s/history/compare?version1=%s&version2=%s'
                % (pub.test_user_class.select()[0].id, *snapshots['user:test']),
            },
        ],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # if elements are not in next bundle, mark them as unistalled
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': '{@'},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [
            {
                'slug': 'test',
                'type': 'forms-categories',
            },
            {
                'slug': 'test',
                'type': 'forms',
            },
            {
                'slug': 'test',
                'type': 'cards-categories',
            },
            {
                'slug': 'test',
                'type': 'cards',
            },
            {
                'slug': 'test',
                'type': 'blocks-categories',
            },
            {
                'slug': 'test',
                'type': 'blocks',
            },
            {
                'slug': 'test',
                'type': 'workflows-categories',
            },
            {
                'slug': 'test',
                'type': 'workflows',
            },
            {
                'slug': 'test',
                'type': 'mail-templates-categories',
            },
            {
                'slug': 'test',
                'type': 'mail-templates',
            },
            {
                'slug': 'test',
                'type': 'comment-templates-categories',
            },
            {
                'slug': 'test',
                'type': 'comment-templates',
            },
            {
                'slug': 'test',
                'type': 'data-sources-categories',
            },
            {
                'slug': 'test',
                'type': 'data-sources',
            },
            {
                'slug': 'test',
                'type': 'wscalls',
            },
            {
                'slug': 'test',
                'type': 'users',
            },
        ],
    }

    # update bundle
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[1])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '37/37 (100%)'

    # and check
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[1])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # snapshots without application info (legacy)
    for snapshot in pub.snapshot_class.select():
        snapshot.application_slug = None
        snapshot.application_version = None
        snapshot.store()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[1])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '17/17 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'no_history_elements': [
            {'slug': 'test', 'type': 'forms-categories'},
            {'slug': 'test', 'type': 'forms'},
            {'slug': 'test', 'type': 'cards-categories'},
            {'slug': 'test', 'type': 'cards'},
            {'slug': 'test', 'type': 'blocks-categories'},
            {'slug': 'test', 'type': 'blocks'},
            {'slug': 'test', 'type': 'workflows-categories'},
            {'slug': 'test', 'type': 'workflows'},
            {'slug': 'test', 'type': 'mail-templates-categories'},
            {'slug': 'test', 'type': 'mail-templates'},
            {'slug': 'test', 'type': 'comment-templates-categories'},
            {'slug': 'test', 'type': 'comment-templates'},
            {'slug': 'test', 'type': 'data-sources-categories'},
            {'slug': 'test', 'type': 'data-sources'},
            {'slug': 'test', 'type': 'wscalls'},
            {'slug': 'test', 'type': 'users'},
        ],
        'unknown_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # bad file format
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'), upload_files=[('bundle', 'bundle.tar', b'garbage')]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.get(afterjob_url.split('/')[-2])
    assert job.failure_label == 'Error: Invalid tar file.'

    # missing manifest
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        foo_fd = io.BytesIO(json.dumps({'foo': 'bar'}, indent=2).encode())
        tarinfo = tarfile.TarInfo('foo.json')
        tarinfo.size = len(foo_fd.getvalue())
        tar.addfile(tarinfo, fileobj=foo_fd)
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.get(afterjob_url.split('/')[-2])
    assert job.failure_label == 'Error: Invalid tar file, missing manifest.'

    # missing component
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'version_number': '42',
            'elements': [{'type': 'forms', 'slug': 'foo', 'name': 'foo'}],
        }
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': json.dumps(['forms/foo'])},
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.get(afterjob_url.split('/')[-2])
    assert job.failure_label == 'Error: Invalid tar file, missing component forms/foo'


def test_export_import_bundle_check_install_only(pub):
    pub.snapshot_class.wipe()
    workflow_category = WorkflowCategory(name='test')
    workflow_category.store()

    workflow = Workflow(name='test')
    workflow.store()

    block_category = BlockCategory(name='test')
    block_category.store()

    block = BlockDef(name='test')
    block.store()

    category = Category(name='Test')
    category.store()

    formdef = FormDef()
    formdef.name = 'Test'
    formdef.store()

    card_category = CardDefCategory(name='Test')
    card_category.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()

    ds_category = DataSourceCategory(name='Test')
    ds_category.store()
    data_source = NamedDataSource(name='Test')
    data_source.store()

    mail_template_category = MailTemplateCategory(name='Test')
    mail_template_category.store()
    mail_template = MailTemplate(name='Test')
    mail_template.store()

    comment_template_category = CommentTemplateCategory(name='Test')
    comment_template_category.store()
    comment_template = CommentTemplate(name='Test')
    comment_template.store()

    wscall = NamedWsCall(name='Test')
    wscall.store()

    bundles = []
    for version in ['1.0', '2.0']:
        bundles.append(
            create_bundle(
                [
                    {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'forms', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'cards', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'blocks', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'workflows', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'mail-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'comment-templates', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources-categories', 'slug': 'test', 'name': 'test'},
                    {'type': 'data-sources', 'slug': 'test', 'name': 'test'},
                    {'type': 'wscalls', 'slug': 'test', 'name': 'test'},
                ],
                ('forms-categories/test', category),
                ('forms/test', formdef),
                ('cards-categories/test', card_category),
                ('cards/test', carddef),
                ('blocks-categories/test', block_category),
                ('blocks/test', block),
                ('workflows-categories/test', workflow_category),
                ('workflows/test', workflow),
                ('data-sources-categories/test', ds_category),
                ('data-sources/test', data_source),
                ('mail-templates-categories/test', mail_template_category),
                ('mail-templates/test', mail_template),
                ('comment-templates-categories/test', comment_template_category),
                ('comment-templates/test', comment_template),
                ('wscalls/test', wscall),
                visible=False,
                version=version,
                config_options={
                    # invalid options are ignored
                    'install_only': {
                        'forms-categories/test': True,
                        'forms/test': True,
                        'cards-categories/test': True,
                        'cards/test': True,
                        'blocks-categories/test': True,
                        'blocks/test': True,
                        'workflows-categories/test': True,
                        'workflows/test': True,
                        'data-sources-categories/test': True,
                        'data-sources/test': True,
                        'mail-templates-categories/test': True,
                        'mail-templates/test': True,
                        'comment-templates-categories/test': True,
                        'comment-templates/test': True,
                        'wscalls/test': True,
                    }
                },
            )
        )

    elements_from_next_bundle = json.dumps(
        [
            'forms-categories/test',
            'forms/test',
            'cards-categories/test',
            'cards/test',
            'blocks-categories/test',
            'blocks/test',
            'workflows-categories/test',
            'workflows/test',
            'data-sources-categories/test',
            'data-sources/test',
            'mail-templates-categories/test',
            'mail-templates/test',
            'comment-templates-categories/test',
            'comment-templates/test',
            'wscalls/test',
        ]
    )

    object_classes = [
        Category,
        FormDef,
        CardDefCategory,
        CardDef,
        BlockCategory,
        BlockDef,
        WorkflowCategory,
        Workflow,
        MailTemplateCategory,
        MailTemplate,
        CommentTemplateCategory,
        CommentTemplate,
        DataSourceCategory,
        NamedDataSource,
        NamedWsCall,
    ]
    for object_class in object_classes:
        object_class.wipe()
    pub.snapshot_class.wipe()

    # not yet imported
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'no_history_elements': [],
        'unknown_elements': [
            {'slug': 'test', 'type': 'forms-categories'},
            {'slug': 'test', 'type': 'cards-categories'},
            {'slug': 'test', 'type': 'blocks-categories'},
            {'slug': 'test', 'type': 'workflows-categories'},
            {'slug': 'test', 'type': 'mail-templates-categories'},
            {'slug': 'test', 'type': 'comment-templates-categories'},
            {'slug': 'test', 'type': 'data-sources-categories'},
        ],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }
    # import bundle
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '34/34 (100%)'
    assert Application.count() == 1
    assert ApplicationElement.count() == 15

    # remove application links
    Application.wipe()
    ApplicationElement.wipe()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'forms-categories',
                'url': 'http://example.net/api/export-import/forms-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'cards-categories',
                'url': 'http://example.net/api/export-import/cards-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'blocks-categories',
                'url': 'http://example.net/api/export-import/blocks-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'test',
                'type': 'workflows-categories',
                'url': 'http://example.net/api/export-import/workflows-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'mail-templates-categories',
                'url': 'http://example.net/api/export-import/mail-templates-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'comment-templates-categories',
                'url': 'http://example.net/api/export-import/comment-templates-categories/test/redirect/',
            },
            {
                'slug': 'test',
                'text': 'Test',
                'type': 'data-sources-categories',
                'url': 'http://example.net/api/export-import/data-sources-categories/test/redirect/',
            },
        ],
        'uninstalled_elements': [],
    }

    # import bundle again, recreate links
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[0])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '34/34 (100%)'
    assert Application.count() == 1
    # links for install-only elements are not recreated, but it is not be possible to delete them
    assert ApplicationElement.count() == 7
    # no changes since last import
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # add local changes
    snapshots = {}
    for object_class in object_classes:
        if object_class in [pub.custom_view_class, pub.role_class]:
            # no snapshot for this objects
            continue
        for obj in object_class.select():
            old_snapshots = pub.snapshot_class.select_object_history(obj)
            obj.name = 'new name !'
            obj.store(comment='local change !')
            new_snapshots = pub.snapshot_class.select_object_history(obj)
            snapshots['%s:%s' % (object_class.xml_root_node, obj.slug)] = (
                old_snapshots[0].id,
                new_snapshots[0].id,
            )
            assert len(new_snapshots) > len(old_snapshots)

    # and check
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[0])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [
            {
                'slug': 'test',
                'type': 'forms-categories',
                'url': 'http://example.net/backoffice/forms/categories/%s/history/compare?version1=%s&version2=%s'
                % (Category.get_by_slug('test').id, *snapshots['category:test']),
            },
            {
                'slug': 'test',
                'type': 'cards-categories',
                'url': 'http://example.net/backoffice/cards/categories/%s/history/compare?version1=%s&version2=%s'
                % (CardDefCategory.get_by_slug('test').id, *snapshots['carddef_category:test']),
            },
            {
                'slug': 'test',
                'type': 'blocks-categories',
                'url': 'http://example.net/backoffice/forms/blocks/categories/%s/history/compare?version1=%s&version2=%s'
                % (BlockCategory.get_by_slug('test').id, *snapshots['block_category:test']),
            },
            {
                'slug': 'test',
                'type': 'workflows-categories',
                'url': 'http://example.net/backoffice/workflows/categories/%s/history/compare?version1=%s&version2=%s'
                % (WorkflowCategory.get_by_slug('test').id, *snapshots['workflow_category:test']),
            },
            {
                'slug': 'test',
                'type': 'mail-templates-categories',
                'url': 'http://example.net/backoffice/workflows/mail-templates/categories/%s/history/compare?version1=%s&version2=%s'
                % (MailTemplateCategory.get_by_slug('test').id, *snapshots['mail_template_category:test']),
            },
            {
                'slug': 'test',
                'type': 'comment-templates-categories',
                'url': 'http://example.net/backoffice/workflows/comment-templates/categories/%s/history/compare?version1=%s&version2=%s'
                % (
                    CommentTemplateCategory.get_by_slug('test').id,
                    *snapshots['comment_template_category:test'],
                ),
            },
            {
                'slug': 'test',
                'type': 'data-sources-categories',
                'url': 'http://example.net/backoffice/forms/data-sources/categories/%s/history/compare?version1=%s&version2=%s'
                % (DataSourceCategory.get_by_slug('test').id, *snapshots['data_source_category:test']),
            },
        ],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # update bundle
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundles[1])]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '34/34 (100%)'

    # and check
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[1])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'unknown_elements': [],
        'no_history_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }

    # snapshots without application info (legacy)
    for snapshot in pub.snapshot_class.select():
        snapshot.application_slug = None
        snapshot.application_version = None
        snapshot.store()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-check/'),
        params={'elements_from_next_bundle': elements_from_next_bundle},
        upload_files=[('bundle', 'bundle.tar', bundles[1])],
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '15/15 (100%)'
    assert resp.json['data']['job_result_data'] == {
        'differences': [],
        'no_history_elements': [
            {'slug': 'test', 'type': 'forms-categories'},
            {'slug': 'test', 'type': 'cards-categories'},
            {'slug': 'test', 'type': 'blocks-categories'},
            {'slug': 'test', 'type': 'workflows-categories'},
            {'slug': 'test', 'type': 'mail-templates-categories'},
            {'slug': 'test', 'type': 'comment-templates-categories'},
            {'slug': 'test', 'type': 'data-sources-categories'},
        ],
        'unknown_elements': [],
        'legacy_elements': [],
        'uninstalled_elements': [],
    }


def test_export_import_workflow_options(pub):
    pub.snapshot_class.wipe()
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='variables')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.workflow = workflow
    formdef.workflow_options = {'foo': 'bar'}
    formdef.store()

    bundle = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
            {'type': 'workflows', 'slug': 'variables', 'name': 'variables'},
        ],
        ('forms/foo', formdef),
        ('workflows/variables', workflow),
    )
    FormDef.wipe()
    Workflow.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '6/6 (100%)'

    # check workflow options are set on first install
    formdef = FormDef.get_by_slug('foo')
    assert formdef.workflow_options == {'foo': 'bar'}

    # check workflow options are not reset on further installs
    formdef.workflow_options = {'foo': 'bar2'}
    formdef.store()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    formdef = FormDef.get_by_slug('foo')
    assert formdef.workflow_options == {'foo': 'bar2'}


def test_api_export_import_invalid_slug(pub):
    pub.snapshot_class.wipe()
    pub.role_class.wipe()
    role1 = pub.role_class(name='Test role 1')
    role1.store()
    role2 = pub.role_class(name='Test role 2')
    role2.store()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.workflow_roles = {'_receiver': role1.id}
    carddef.backoffice_submission_roles = [role2.id]
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/cards/test/dependencies/'))
    assert {x['text'] for x in resp.json['data']} == {'Test role 1', 'Test role 2'}

    role2.slug = 'test role 2'  # invalid slug
    role2.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/cards/test/dependencies/'))
    assert {x['text'] for x in resp.json['data']} == {'Test role 1'}


def test_export_import_with_missing_role(pub):
    pub.snapshot_class.wipe()
    AfterJob.wipe()
    FormDef.wipe()
    Workflow.wipe()

    pub.cfg['sp'] = {'idp-manage-roles': True}  # do not automatically recreate roles
    pub.write_cfg()

    workflow = Workflow(name='test')
    st = workflow.add_status('st')
    action = st.add_action('sendmail')
    action.to = ['invalid']
    workflow.store()

    bundle = create_bundle(
        [
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
        ],
        ('workflows/test', workflow),
    )

    Workflow.wipe()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'failed'
    job = AfterJob.select(order_by='creation_time')[0]
    assert job.failure_label == 'Error: Unknown referenced objects (Unknown roles: invalid)'


def test_export_import_with_mail_template(pub):
    pub.snapshot_class.wipe()
    AfterJob.wipe()
    MailTemplate.wipe()
    Workflow.wipe()

    mail_template = MailTemplate(name='test mail template')
    mail_template.store()

    workflow = Workflow(name='test')
    status = workflow.add_status('New')
    send_mail = status.add_action('sendmail')
    send_mail.to = ['test@localhost']
    send_mail.mail_template = mail_template.slug
    workflow.store()

    bundle = create_bundle(
        [
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
            {'type': 'mail-templates', 'slug': mail_template.slug, 'name': mail_template.name},
        ],
        ('workflows/test', workflow),
        (f'mail-templates/{mail_template.slug}', mail_template),
    )
    Workflow.wipe()
    MailTemplate.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert MailTemplate.count() == 1
    assert Workflow.count() == 1
    mail_template = MailTemplate.select()[0]
    workflow = Workflow.select()[0]
    assert workflow.possible_status[0].items[0].mail_template == mail_template.slug


def test_export_import_with_customview_in_global_action(pub):
    pub.snapshot_class.wipe()
    AfterJob.wipe()
    Workflow.wipe()
    CardDef.wipe()
    pub.custom_view_class.wipe()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'test'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    workflow = Workflow(name='test')
    ac1 = workflow.add_global_action('Action', 'ac1')
    display_form = ac1.add_action('form', id='_form')
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    ds = {'type': 'carddef:%s:test' % carddef.url_name}
    display_form.formdef.fields = [
        ItemField(id='0', label='string', data_source=ds),
    ]
    workflow.store()

    bundle = create_bundle(
        [
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
            {'type': 'cards', 'slug': carddef.url_name, 'name': carddef.name},
        ],
        ('workflows/test', workflow),
        (f'cards/{carddef.url_name}', carddef),
    )
    Workflow.wipe()
    CardDef.wipe()
    pub.custom_view_class.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert CardDef.count() == 1
    assert Workflow.count() == 1
    workflow = Workflow.select()[0]
    assert workflow.global_actions[0].items[0].formdef.fields[0].data_source == ds


def test_export_import_card_data(pub):
    pub.snapshot_class.wipe()
    AfterJob.wipe()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [StringField(id='1', label='Test')]
    carddef.store()

    for i in range(10):
        carddata = carddef.data_class()()
        carddata.data = {'1': 'data %s' % i}
        carddata.just_created()
        carddata.store()

    assert len(ApplicationCardData.select()) == 1
    application_carddata = ApplicationCardData.select()[0]
    assert list(application_carddata.get_dependencies()) == [carddef]
    assert application_carddata.get_admin_url() == 'http://example.net/backoffice/data/test/'

    bundle = create_bundle(
        [
            {'type': 'cards', 'slug': 'test', 'name': 'Test'},
            {'type': 'cards-data', 'slug': 'test', 'name': 'Test'},
        ],
        ('cards/test', carddef),
        ('cards-data/test', ApplicationCardData(carddef)),
    )
    carddef.data_class().wipe()

    # import as new
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['completion_status'] == '4/4 (100%)'
    assert {x.data['1'] for x in carddef.data_class().select()} == {'data %s' % x for x in range(10)}

    # import and update
    for i, carddata in enumerate(carddef.data_class().select()):
        carddata.data = {'1': 'modified data %s' % i}
        carddata.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    assert {x.data['1'] for x in carddef.data_class().select()} == {'data %s' % x for x in range(10)}


def test_export_import_remap_deleted_status(pub):
    AfterJob.wipe()
    pub.snapshot_class.wipe()

    workflow = Workflow(name='test')
    workflow.add_status('status 1')
    st2 = workflow.add_status('status 2')
    workflow.status_remapping = {
        'xxx': {
            'action': f'reassign-{st2.id}',
            'status': 'xxx',
            'timestamp': localtime().isoformat(),
        },
        'yyy': {
            'action': 'remove',
            'status': 'yyy',
            'timestamp': localtime().isoformat(),
        },
    }
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.status = 'wf-xxx'
    formdata2.store()

    formdata3 = formdef.data_class()()
    formdata3.just_created()
    formdata3.status = 'wf-yyy'
    formdata3.store()

    bundle = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
        ],
        ('forms/foo', formdef),
        ('workflows/test', workflow),
    )

    AfterJob.wipe()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    formdata2.refresh_from_storage()
    assert formdata2.status == f'wf-{st2.id}'

    with pytest.raises(KeyError):
        formdata3.refresh_from_storage()

    workflow.refresh_from_storage()
    assert len(workflow.status_remapping_done) == 2
    assert [x.comment for x in pub.snapshot_class.select_object_history(workflow)] == [
        'Application (Test) workflow status migration',
        'Application (Test) update',
        None,
    ]

    # execute again, migrations will not be run again
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    assert [x.comment for x in pub.snapshot_class.select_object_history(workflow)] == [
        'Application (Test) update',
        'Application (Test) workflow status migration',
        'Application (Test) update',
        None,
    ]


def test_export_import_workflow_change(pub):
    AfterJob.wipe()
    pub.snapshot_class.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('status 1')
    st1.add_action('geolocate')
    st2 = workflow.add_status('status 2')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.status = 'wf-new'
    formdata1.store()

    formdef.workflow = workflow
    workflow_migrations = formdef.workflow_migrations = {
        '_default test': {
            'old_workflow': '_default',
            'new_workflow': 'test',
            'status_mapping': {
                'just_submitted': st1.id,
                'new': st1.id,
                'accepted': st2.id,
                'rejected': st2.id,
                'finished': st2.id,
            },
            'timestamp': '2024-06-19T13:26:45.298551+02:00',
        }
    }
    formdef.store()

    bundle = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
        ],
        ('forms/foo', formdef),
        ('workflows/test', workflow),
    )

    # alter formdef to a supposed initial state
    formdef.name = 'bar'
    formdef.workflow = Workflow.get_default_workflow()
    formdef.workflow_migrations = None
    formdef.store()

    Workflow.wipe()
    AfterJob.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    formdef.refresh_from_storage()
    assert formdef.name == 'foo'
    assert formdef.workflow.slug == 'test'
    assert formdef.workflow_migrations == workflow_migrations
    # check geolocations has been enabled on the formdef; without
    # explicit "Geolocation enabled by workflow" in snapshot history
    # this indicates the change_workflow() happened after the workflow
    # has been updated.
    assert formdef.geolocations == {'base': 'Geolocation'}
    assert [x.comment for x in pub.snapshot_class.select_object_history(formdef) if x.comment] == [
        'Application (Test) update',
        'Application (Test) update, workflow change',
    ]

    # check formdata status got changed
    formdata1.refresh_from_storage()
    assert formdata1.status == f'wf-{st1.id}'

    # create bundle with workflow change but no migration
    formdef.workflow_migrations = None
    formdef.workflow = workflow
    formdef.store()

    bundle = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
            {'type': 'workflows', 'slug': 'test', 'name': 'test'},
        ],
        ('forms/foo', formdef),
        ('workflows/test', workflow),
    )

    # alter formdef to a supposed initial state
    formdef.name = 'bar'
    formdef.workflow = Workflow.get_default_workflow()
    formdef.workflow_migrations = None
    formdef.store()

    Workflow.wipe()
    AfterJob.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'


def test_export_import_digest_change(pub):
    CardDef.wipe()
    AfterJob.wipe()
    pub.snapshot_class.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [StringField(id='1', label='label', varname='label')]
    carddef.digest_templates = {'default': '{{form_var_label}}'}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'foo'}
    carddata.just_created()
    carddata.store()
    assert carddata.digests == {'default': 'foo'}

    bundle = create_bundle(
        [
            {'type': 'cards', 'slug': 'foo', 'name': 'foo'},
        ],
        ('cards/foo', carddef),
    )

    carddef.digest_templates = {'default': 'x{{form_var_label}}'}
    carddef.store()
    carddata = carddef.data_class().get(carddata.id)
    carddata.store()
    assert carddata.digests == {'default': 'xfoo'}

    AfterJob.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    carddef.refresh_from_storage()
    assert carddef.digest_templates == {'default': '{{form_var_label}}'}
    for carddata in carddef.data_class().select():
        assert carddata.digests['default'] == f'{carddata.data["1"]}'


def test_export_import_statistics_change(pub):
    CardDef.wipe()
    AfterJob.wipe()
    pub.snapshot_class.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [ItemField(id='1', label='item', varname='item', display_locations=['statistics'])]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'foo'}
    carddata.just_created()
    carddata.store()
    assert carddata.statistics_data

    # empty it
    carddata.statistics_data = {}
    carddata.update_column('statistics_data')

    bundle = create_bundle(
        [
            {'type': 'cards', 'slug': 'foo', 'name': 'foo'},
        ],
        ('cards/foo', carddef),
    )

    carddef.fields[0].display_locations = []
    carddef.store()
    carddata = carddef.data_class().get(carddata.id)
    assert not carddata.statistics_data

    AfterJob.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    carddata.refresh_from_storage()
    assert carddata.statistics_data['item'] == ['foo']


def test_export_import_statistics_in_block_change(pub):
    BlockDef.wipe()
    CardDef.wipe()
    AfterJob.wipe()
    pub.snapshot_class.wipe()

    blockdef = BlockDef()
    blockdef.name = 'foo'
    blockdef.fields = [ItemField(id='1', label='item', varname='item', display_locations=['statistics'])]
    blockdef.store()

    carddef = CardDef()
    carddef.name = 'bar'
    carddef.fields = [BlockField(id='1', label='block', block_slug='foo')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': {'data': [{'1': 'a', '1_display': 'A'}]}, 'schema': {}}
    carddata.just_created()
    carddata.store()
    assert carddata.statistics_data

    # empty it
    carddata.statistics_data = {}
    carddata.update_column('statistics_data')

    bundle = create_bundle(
        [
            {'type': 'blocks', 'slug': 'foo', 'name': 'foo'},
        ],
        ('blocks/foo', blockdef),
    )

    blockdef.fields[0].display_locations = []
    blockdef.store()
    carddata = carddef.data_class().get(carddata.id)
    assert not carddata.statistics_data

    AfterJob.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    carddata.refresh_from_storage()
    assert carddata.statistics_data['item'] == ['a']


@pytest.mark.parametrize('obj_type', ['forms', 'cards', 'blocks'])
def test_export_import_keep_some_form_changes_on_update(pub, obj_type):
    FormDef.wipe()
    CardDef.wipe()
    BlockDef.wipe()
    pub.snapshot_class.wipe()

    klass = {'forms': FormDef, 'cards': CardDef, 'blocks': BlockDef}.get(obj_type)

    formdef = klass()
    formdef.name = 'foo'
    formdef.fields = [
        PageField(id='0', label='page'),  # PageField is invalid in blocks but this is ignored here
        StringField(id='1', label='string1', required='required'),
        StringField(id='2', label='string2', required='required'),
    ]
    formdef.store()

    bundle_v1 = create_bundle(
        [
            {'type': obj_type, 'slug': 'foo', 'name': 'foo'},
        ],
        (f'{obj_type}/foo', formdef),
        version_number='1.0',
    )

    formdef.name = 'foo2'
    formdef.fields = [
        PageField(id='0', label='page'),
        StringField(id='1', label='string1', required='optional'),
        StringField(id='2', label='string2', required='required'),
    ]
    formdef.store()
    bundle_v2 = create_bundle(
        [
            {'type': obj_type, 'slug': 'foo', 'name': 'foo2'},
        ],
        (f'{obj_type}/foo', formdef),
        version_number='2.0',
    )

    formdef.name = 'foo3'
    formdef.fields = [
        PageField(id='0', label='page'),
        StringField(id='1', label='string1', required='optional'),
        StringField(id='2', label='string2', required='optional'),
    ]
    formdef.store()
    bundle_v3 = create_bundle(
        [
            {'type': obj_type, 'slug': 'foo', 'name': 'foo3'},
        ],
        (f'{obj_type}/foo', formdef),
        version_number='3.0',
    )

    klass.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v1)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    # check form title
    formdef = klass.get_by_slug('foo')
    assert formdef.name == 'foo'

    # check updates are applying the changes
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v2)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    formdef = klass.get_by_slug('foo')
    assert formdef.name == 'foo2'
    assert [getattr(x, 'required', None) for x in formdef.fields] == [None, 'optional', 'required']

    # check local changes are not reset
    formdef.name = 'foo local change'
    formdef.fields[1].required = True
    formdef.fields[2].label = 'Changed'
    formdef.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v3)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    formdef = klass.get_by_slug('foo')
    assert formdef.name == 'foo local change'
    assert [getattr(x, 'required', None) for x in formdef.fields] == [None, 'required', 'optional']
    assert formdef.fields[2].label == 'Changed'

    # reinstall application, local changes should be kept
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v3)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'
    formdef = klass.get_by_slug('foo')
    assert formdef.name == 'foo local change'


def test_export_import_do_not_touch_category_roles_on_update(pub):
    FormDef.wipe()
    pub.snapshot_class.wipe()

    role = pub.role_class(name='Test role')
    role.uuid = str(uuid.uuid4())
    role.store()
    role2 = pub.role_class(name='Other test role')
    role2.uuid = str(uuid.uuid4())
    role2.store()

    category = Category(name='test')
    category.management_roles = [role]
    category.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.category_id = category.id
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/export-import/forms-categories/'))
    resp = get_app(pub).get(sign_uri(resp.json['data'][0]['urls']['dependencies']))
    assert [x['uuid'] for x in resp.json['data']] == [str(role.uuid)]

    bundle_v1 = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
            {'type': 'forms-categories', 'slug': 'test', 'name': 'test'},
        ],
        ('forms-categories/test', category),
        ('forms/foo', formdef),
        version_number='1.0',
    )

    Category.wipe()
    FormDef.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v1)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    category = Category.select()[0]
    assert category.management_roles == [role]

    # check local changes are not reset
    category.management_roles = [role2]
    category.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v1)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    category = Category.select()[0]
    assert category.management_roles == [role2]


def test_export_import_replace_custom_views_on_update(pub):
    FormDef.wipe()
    pub.snapshot_class.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared formdef custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    bundle_v1 = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
        ],
        ('forms/foo', formdef),
        version_number='1.0',
    )

    bundle_v2 = create_bundle(
        [
            {'type': 'forms', 'slug': 'foo', 'name': 'foo'},
        ],
        ('forms/foo', formdef),
        version_number='2.0',
    )

    FormDef.wipe()
    pub.custom_view_class.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v1)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    assert FormDef.count() == 1
    assert pub.custom_view_class.count() == 1

    custom_view = pub.custom_view_class.select()[0]
    custom_view.title = 'modified shared formdef custom view'
    custom_view.visibility = 'role'
    custom_view.store()

    private_custom_view = pub.custom_view_class()
    private_custom_view.title = 'private formdef custom view'
    private_custom_view.formdef = formdef
    private_custom_view.columns = {'list': [{'id': '1'}]}
    private_custom_view.filters = {}
    private_custom_view.visibility = 'owner'
    private_custom_view.store()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_v2)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    assert FormDef.count() == 1
    assert pub.custom_view_class.count() == 2
    reloaded_custom_view = pub.custom_view_class.get_by_slug(custom_view.slug)
    assert reloaded_custom_view.title == 'shared formdef custom view'
    assert reloaded_custom_view.visibility == 'any'
    private_custom_view.refresh_from_storage()  # no change for private view


def test_export_import_block_and_card_custom_view(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()
    pub.snapshot_class.wipe()

    carddef = CardDef()
    carddef.name = 'test card'
    carddef.fields = [StringField(id='1', label='label', varname='label')]
    carddef.digest_templates = {'default': '{{form_var_label}}'}
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'data source custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '1'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    blockdef = BlockDef(name='bar')
    blockdef.fields = [
        ItemField(id='1', label='choice', data_source={'type': f'carddef:{carddef.slug}:{custom_view.slug}'})
    ]
    blockdef.store()

    formdef = FormDef()
    formdef.name = 'foo'
    carddef.fields = [BlockField(id='1', label='block', block_slug='bar')]
    formdef.store()

    bundle_1 = create_bundle(
        [
            {'type': 'forms', 'slug': formdef.slug, 'name': formdef.name},
            {'type': 'blocks', 'slug': blockdef.slug, 'name': blockdef.name},
            {'type': 'cards', 'slug': carddef.slug, 'name': carddef.name},
        ],
        (f'forms/{formdef.slug}', formdef),
        (f'blocks/{blockdef.slug}', blockdef),
        (f'cards/{carddef.slug}', carddef),
        version_number='1.0',
    )

    # change custom view
    custom_view.slug = 'changed'
    custom_view.store()
    blockdef.fields = [
        ItemField(id='1', label='choice', data_source={'type': f'carddef:{carddef.slug}:{custom_view.slug}'})
    ]
    blockdef.store()

    bundle_2 = create_bundle(
        [
            {'type': 'forms', 'slug': formdef.slug, 'name': formdef.name},
            {'type': 'blocks', 'slug': blockdef.slug, 'name': blockdef.name},
            {'type': 'cards', 'slug': carddef.slug, 'name': carddef.name},
        ],
        (f'forms/{formdef.slug}', formdef),
        (f'blocks/{blockdef.slug}', blockdef),
        (f'cards/{carddef.slug}', carddef),
        version_number='1.0',
    )

    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_1)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'

    assert BlockDef.count() == 1
    assert CardDef.count() == 1
    assert FormDef.count() == 1
    assert pub.custom_view_class.count() == 1

    pub.custom_view_class.wipe()
    resp = get_app(pub).post(
        sign_uri('/api/export-import/bundle-import/'), upload_files=[('bundle', 'bundle.tar', bundle_2)]
    )
    afterjob_url = resp.json['url']
    resp = get_app(pub).put(sign_uri(afterjob_url))
    assert resp.json['data']['status'] == 'completed'


def test_export_import_uninstall(pub):
    pub.snapshot_class.wipe()
    application = Application()
    application.slug = 'test'
    application.name = 'Test'
    application.version_number = 'foo'
    application.store()

    other_application = Application()
    other_application.slug = 'other-test'
    other_application.name = 'Other Test'
    other_application.version_number = 'foo'
    other_application.store()

    formdef1 = FormDef()
    formdef1.name = 'Formdef1'
    formdef1.store()
    ApplicationElement.update_or_create_for_object(application, formdef1)

    formdef2 = FormDef()
    formdef2.name = 'Formdef2'
    formdef2.store()
    ApplicationElement.update_or_create_for_object(application, formdef2)

    # delete formdef2, ApplicationElement will point to an empty object
    formdef2.remove_self()

    # add formdef3 to second app
    formdef3 = FormDef()
    formdef3.name = 'Formdef3'
    formdef3.store()
    ApplicationElement.update_or_create_for_object(other_application, formdef3)

    app = get_app(pub)
    resp = app.post(sign_uri('/api/export-import/uninstall-check/'), {'application': 'test'})
    assert resp.json == {'err': 0}

    resp = app.post(sign_uri('/api/export-import/uninstall-check/'), {'application': 'missing'})
    assert resp.json == {'err': 0}

    resp = app.post(sign_uri('/api/export-import/uninstall/'), {'application': 'missing'})
    assert resp.json == {'err': 0}

    formdata = formdef1.data_class()()
    formdata.just_created()
    formdata.store()
    resp = app.post(sign_uri('/api/export-import/uninstall-check/'), {'application': 'test'})
    assert resp.json == {'err': 1, 'err_desc': 'Existing data in "Formdef1"'}

    resp = app.post(sign_uri('/api/export-import/uninstall/'), {'application': 'test'})
    assert resp.json == {'err': 0}
    assert {x.slug for x in FormDef.select()} == {'formdef3'}
    assert {x.slug for x in Application.select()} == {'other-test'}
