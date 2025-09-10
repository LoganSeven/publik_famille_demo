import base64
import json
import os
import time
import urllib.parse

import pytest
import responses
from django.utils.encoding import force_str
from quixote import get_publisher

from wcs import fields, qommon
from wcs.admin.settings import UserFieldsFormDef
from wcs.api_utils import sign_url
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
from .utils import sign_uri


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
            '''\
[api-secrets]
coucou = 1234
'''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_cards(pub, local_user):
    AfterJob.wipe()
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    CardDefCategory.wipe()
    category = CardDefCategory()
    category.name = 'Category A'
    category.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foo')]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.digest_templates = {'default': 'bla {{ form_var_foo }} xxx'}
    carddef.store()

    carddef.data_class().wipe()
    formdata = carddef.data_class()()
    formdata.data = {'0': 'blah'}
    formdata.just_created()
    formdata.store()
    assert formdata.digests == {'default': 'bla blah xxx'}

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.user = local_user
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    resp = get_app(pub).get('/api/cards/@list', status=403)
    resp = get_app(pub).get(sign_uri('/api/cards/@list'))
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['slug'] == 'test'
    assert resp.json['data'][0]['category_slug'] is None
    assert resp.json['data'][0]['category_name'] is None
    assert resp.json['data'][0]['custom_views'] == [
        {'id': 'datasource-carddef-custom-view', 'text': 'datasource carddef custom view'},
        {'id': 'shared-carddef-custom-view', 'text': 'shared carddef custom view'},
    ]

    carddef.category = category
    carddef.store()
    resp = get_app(pub).get(sign_uri('/api/cards/@list'))
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['slug'] == 'test'
    assert resp.json['data'][0]['category_slug'] == 'category-a'
    assert resp.json['data'][0]['category_name'] == 'Category A'

    # signed but anonymous
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?NameID='), status=403)

    # signed without specifying any user -> get everything
    resp = get_app(pub).get(sign_uri('/api/cards/test/list'))
    assert len(resp.json['data']) == 1

    resp = get_app(pub).get(sign_uri('/api/cards/test/list?NameID=%s' % local_user.name_identifiers[0]))
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['display_id'] == formdata.get_display_id()
    assert resp.json['data'][0]['display_name'] == formdata.get_display_name()
    assert resp.json['data'][0]['digest'] == formdata.digests['default']
    assert resp.json['data'][0]['text'] == formdata.digests['default']
    resp = get_app(pub).get(
        sign_uri('/api/cards/test/list?NameID=%s&full=on' % local_user.name_identifiers[0])
    )
    assert resp.json['data'][0]['fields']['foo'] == 'blah'
    assert resp.json['data'][0]['digest'] == formdata.digests['default']
    assert resp.json['data'][0]['text'] == formdata.digests['default']

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/datasource-carddef-custom-view?NameID=%s' % local_user.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['display_id'] == formdata.get_display_id()
    assert resp.json['data'][0]['display_name'] == formdata.get_display_name()
    assert resp.json['data'][0]['digest'] == formdata.digests['default']
    assert resp.json['data'][0]['text'] == formdata.digests['default']
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/datasource-carddef-custom-view?NameID=%s&full=on'
            % local_user.name_identifiers[0]
        )
    )
    assert resp.json['data'][0]['fields']['foo'] == 'blah'
    assert resp.json['data'][0]['digest'] == formdata.digests['default']
    assert resp.json['data'][0]['text'] == formdata.digests['default']

    # with custom digest template
    carddef.digest_templates = {
        'default': 'bla {{ form_var_foo }} xxx',
        'custom-view:datasource-carddef-custom-view': '{{ form_var_foo }}',
    }
    carddef.store()
    formdata.store()
    assert formdata.digests == {
        'default': 'bla blah xxx',
        'custom-view:datasource-carddef-custom-view': 'blah',
    }
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/datasource-carddef-custom-view?NameID=%s' % local_user.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['display_id'] == formdata.get_display_id()
    assert resp.json['data'][0]['display_name'] == formdata.get_display_name()
    assert resp.json['data'][0]['digest'] == formdata.digests['custom-view:datasource-carddef-custom-view']
    assert resp.json['data'][0]['text'] == formdata.digests['custom-view:datasource-carddef-custom-view']
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/datasource-carddef-custom-view?NameID=%s&full=on'
            % local_user.name_identifiers[0]
        )
    )
    assert resp.json['data'][0]['fields']['foo'] == 'blah'
    assert resp.json['data'][0]['digest'] == formdata.digests['custom-view:datasource-carddef-custom-view']
    assert resp.json['data'][0]['text'] == formdata.digests['custom-view:datasource-carddef-custom-view']

    # get single carddata (as signed request without any user specified, so
    # no check for permissions)
    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % formdata.id))
    assert resp.json['text'] == formdata.digests['default']
    assert AfterJob.count() == 0


def test_carddef_schema(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foo')]
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/@schema'), status=200)
    assert len(resp.json['fields']) == 1
    assert resp.json['fields'][0]['label'] == 'foobar'
    assert resp.json['fields'][0]['varname'] == 'foo'
    assert resp.json['user']['fields'] == [
        {'label': 'Full name', 'type': 'string', 'varname': 'name'},
        {'label': 'Email', 'type': 'email', 'varname': 'email'},
    ]

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='3', label='test', varname='var1'),
        fields.StringField(id='9', label='noop', varname='var2'),
        fields.StringField(id='42', label='no varname'),
    ]
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/@schema'), status=200)
    assert resp.json['user']['fields'] == [
        {'label': 'Full name', 'type': 'string', 'varname': 'name'},
        {'label': 'Email', 'type': 'email', 'varname': 'email'},
        {'label': 'test', 'type': 'string', 'varname': 'var1'},
        {'label': 'noop', 'type': 'string', 'varname': 'var2'},
        {'label': 'no varname', 'type': 'string', 'varname': ''},
    ]

    resp = get_app(pub).get('/api/cards/test/@schema', status=403)


def test_carddef_schema_global_actions(pub):
    CardDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='test')
    role.store()

    workflow = Workflow(name='test-workflow')
    workflow.add_status('dummy-status')
    action = workflow.add_global_action('Test Global Action')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'test-trigger'
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test-actions'
    carddef.workflow = workflow
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    assert resp.json['workflow']['actions'] == {
        'global-action:test-trigger': {'label': 'Test Global Action (test-trigger)'}
    }

    trigger.identifier = None
    workflow.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    assert resp.json['workflow']['actions'] == {}


def test_carddef_schema_jump_triggers(pub):
    CardDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='test')
    role.store()

    workflow = Workflow(name='test-workflow')
    source_status = workflow.add_status('source-status')
    workflow.add_status('target-status')

    jump = source_status.add_action('jump')
    jump.trigger = 'test-trigger'

    workflow.store()

    carddef = CardDef()
    carddef.name = 'test-actions'
    carddef.workflow = workflow
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    assert resp.json['workflow']['actions'] == {
        'jump:test-trigger': {'label': 'source-status (test-trigger)'}
    }

    jump.trigger = None
    workflow.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    assert resp.json['workflow']['actions'] == {}


@pytest.mark.parametrize('flag', [True, False])
def test_carddef_schema_editable_action(pub, flag):
    pub.load_site_options()
    pub.site_options.add_section('options')
    pub.site_options.set('options', 'api-include-editable-action', 'true' if flag else 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    CardDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='test')
    role.store()

    workflow = Workflow(name='test-workflow')
    source_status = workflow.add_status('source-status')
    workflow.add_status('target-status')

    edit = source_status.add_action('editable')
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test-actions'
    carddef.workflow = workflow
    carddef.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    if flag:
        assert resp.json['workflow']['actions'] == {'link:edit:1-1': {'label': 'Edit (source-status)'}}
    else:
        assert resp.json['workflow']['actions'] == {}

    edit.label = 'Edit this card'
    workflow.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test-actions/@schema'), status=200)
    if flag:
        assert resp.json['workflow']['actions'] == {
            'link:edit:1-1': {'label': 'Edit this card (source-status)'}
        }
    else:
        assert resp.json['workflow']['actions'] == {}


def test_carddef_schema_user_cards_datasource(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.user_support = 'optional'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef.store()

    for value in ['foo', 'bar', 'baz']:
        carddata = carddef.data_class()()
        carddata.data = {'0': value}
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    carddef2 = CardDef()
    carddef2.name = 'foobar'
    carddef2.fields = [
        fields.ItemField(id='0', label='item', varname='foo', items=['a', 'b'], data_source=ds),
    ]
    carddef2.store()

    resp = get_app(pub).get(sign_uri('/api/cards/foobar/@schema'), status=200)
    assert 'items' not in resp.json['fields'][0]
    assert resp.json['fields'][0]['items_url'] == 'http://example.net/api/cards/items/list'

    carddef2.fields[0].data_source['type'] = 'carddef:%s:_with_user_filter' % carddef.url_name
    carddef2.store()
    resp = get_app(pub).get(sign_uri('/api/cards/foobar/@schema'), status=200)
    assert 'items_url' not in resp.json['fields'][0]

    carddef2.fields[0].data_source['type'] = 'carddef:%s:custom' % carddef.url_name
    carddef2.store()
    resp = get_app(pub).get(sign_uri('/api/cards/foobar/@schema'), status=200)
    assert resp.json['fields'][0]['items_url'] == 'http://example.net/api/cards/items/custom/list'


def test_carddef_schema_relations(pub):
    FormDef.wipe()
    CardDef.wipe()
    BlockDef.wipe()

    formdef = FormDef()
    formdef.name = 'formdef'
    formdef.fields = [
        fields.ItemField(id='0', label='Unknown', varname='unknown', data_source={'type': 'carddef:unknown'}),
        fields.ItemField(id='1', label='Card 1', data_source={'type': 'carddef:carddef-1'}),
        fields.ItemField(id='2', label='Card 2', varname='card2', data_source={'type': 'carddef:carddef-2'}),
        fields.ItemsField(id='3', label='Cards 1', data_source={'type': 'carddef:carddef-1'}),
        fields.ItemsField(
            id='4', label='Cards 2', varname='cards2', data_source={'type': 'carddef:carddef-2'}
        ),
        fields.ComputedField(
            id='5',
            label='Computed card 2',
            varname='computed_card2',
            data_source={'type': 'carddef:carddef-2'},
        ),
    ]
    formdef.store()

    carddef1 = CardDef()
    carddef1.name = 'carddef 1'
    carddef1.fields = [
        fields.ItemField(id='0', label='Unknown', varname='unknown', data_source={'type': 'carddef:unknown'}),
        fields.ItemField(id='1', label='Card 2', varname='card2', data_source={'type': 'carddef:carddef-2'}),
        fields.ItemField(id='2', label='Card 3', data_source={'type': 'carddef:carddef-3'}),  # no varname
        fields.ItemsField(
            id='3', label='Cards 2', varname='cards2', data_source={'type': 'carddef:carddef-2'}
        ),
        fields.ItemsField(id='4', label='Cards 3', data_source={'type': 'carddef:carddef-3'}),  # no varname
        fields.ComputedField(
            id='5',
            label='Computed card 2',
            varname='computed_card2',
            data_source={'type': 'carddef:carddef-2'},
        ),
    ]
    carddef1.store()

    carddef2 = CardDef()
    carddef2.name = 'carddef 2'
    carddef2.store()

    carddef3 = CardDef()
    carddef3.name = 'carddef 3'
    carddef3.fields = [
        # no varnames
        fields.ItemField(id='1', label='Card2', data_source={'type': 'carddef:carddef-2'}),
        fields.ItemsField(id='2', label='Cards 2', data_source={'type': 'carddef:carddef-2'}),
    ]
    carddef3.store()

    resp = get_app(pub).get(sign_uri('/api/cards/carddef-1/@schema'), status=200)
    assert resp.json['relations'] == [
        {'varname': 'card2', 'label': 'Card 2', 'type': 'item', 'obj': 'carddef:carddef-2', 'reverse': False},
        {
            'varname': 'cards2',
            'label': 'Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
        {
            'varname': 'computed_card2',
            'label': 'Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
    ]
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-2/@schema'), status=200)
    assert resp.json['relations'] == [
        {'varname': 'card2', 'label': 'Card 2', 'type': 'item', 'obj': 'carddef:carddef-1', 'reverse': True},
        {
            'varname': 'cards2',
            'label': 'Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'computed_card2',
            'label': 'Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
    ]

    # custom views ?
    carddef1.fields[1] = fields.ItemField(
        id='1', label='Card 2', varname='card2', data_source={'type': 'carddef:carddef-2:view'}
    )
    carddef1.store()
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-1/@schema'), status=200)
    assert resp.json['relations'] == [
        {'varname': 'card2', 'label': 'Card 2', 'type': 'item', 'obj': 'carddef:carddef-2', 'reverse': False},
        {
            'varname': 'cards2',
            'label': 'Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
        {
            'varname': 'computed_card2',
            'label': 'Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
    ]
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-2/@schema'), status=200)
    assert resp.json['relations'] == [
        {'varname': 'card2', 'label': 'Card 2', 'type': 'item', 'obj': 'carddef:carddef-1', 'reverse': True},
        {
            'varname': 'cards2',
            'label': 'Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'computed_card2',
            'label': 'Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
    ]

    # circular relation ?
    carddef1.fields = [
        fields.ItemField(id='1', label='Card 1', varname='card1', data_source={'type': 'carddef:carddef-1'}),
    ]
    carddef1.store()
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-1/@schema'), status=200)
    assert resp.json['relations'] == [
        {'varname': 'card1', 'label': 'Card 1', 'type': 'item', 'obj': 'carddef:carddef-1', 'reverse': False},
        {'varname': 'card1', 'label': 'Card 1', 'type': 'item', 'obj': 'carddef:carddef-1', 'reverse': True},
    ]
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-2/@schema'), status=200)
    assert resp.json['relations'] == []

    # block field
    block1 = BlockDef()
    block1.name = 'block 1'
    block1.fields = [
        fields.ItemField(id='0', label='Unknown', varname='unknown', data_source={'type': 'carddef:unknown'}),
        fields.ItemField(
            id='1',
            label='Card 1',
            varname='card1',
            data_source={'type': 'carddef:carddef-1'},
        ),
        fields.ItemField(
            id='2',
            label='Card 2',
            varname='card2',
            data_source={'type': 'carddef:carddef-2'},
        ),
        fields.ItemsField(
            id='3', label='Cards 1', varname='cards1', data_source={'type': 'carddef:carddef-1'}
        ),
        fields.ItemsField(
            id='4', label='Cards 2', varname='cards2', data_source={'type': 'carddef:carddef-2'}
        ),
        fields.ComputedField(
            id='5',
            label='Computed card 1',
            varname='computed_card1',
            data_source={'type': 'carddef:carddef-1'},
        ),
        fields.ComputedField(
            id='6',
            label='Computed card 2',
            varname='computed_card2',
            data_source={'type': 'carddef:carddef-2'},
        ),
    ]
    block1.store()

    # no varname on block field
    carddef1.fields = [fields.BlockField(id='1', label='Block 1', block_slug=block1.slug)]
    carddef1.store()
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-1/@schema'), status=200)
    assert resp.json['relations'] == []
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-2/@schema'), status=200)
    assert resp.json['relations'] == []

    # with varname
    carddef1.fields = [fields.BlockField(id='1', label='Block 1', varname='block1', block_slug=block1.slug)]
    carddef1.store()
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-1/@schema'), status=200)
    assert resp.json['relations'] == [
        {
            'varname': 'block1_card1',
            'label': 'Block 1 - Card 1',
            'type': 'item',
            'obj': 'carddef:carddef-1',
            'reverse': False,
        },
        {
            'varname': 'block1_card1',
            'label': 'Block 1 - Card 1',
            'type': 'item',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'block1_card2',
            'label': 'Block 1 - Card 2',
            'type': 'item',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
        {
            'varname': 'block1_cards1',
            'label': 'Block 1 - Cards 1',
            'type': 'items',
            'obj': 'carddef:carddef-1',
            'reverse': False,
        },
        {
            'varname': 'block1_cards1',
            'label': 'Block 1 - Cards 1',
            'type': 'items',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'block1_cards2',
            'label': 'Block 1 - Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
        {
            'varname': 'block1_computed_card1',
            'label': 'Block 1 - Computed card 1',
            'type': 'computed',
            'obj': 'carddef:carddef-1',
            'reverse': False,
        },
        {
            'varname': 'block1_computed_card1',
            'label': 'Block 1 - Computed card 1',
            'type': 'computed',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'block1_computed_card2',
            'label': 'Block 1 - Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-2',
            'reverse': False,
        },
    ]
    resp = get_app(pub).get(sign_uri('/api/cards/carddef-2/@schema'), status=200)
    assert resp.json['relations'] == [
        {
            'varname': 'block1_card2',
            'label': 'Block 1 - Card 2',
            'type': 'item',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'block1_cards2',
            'label': 'Block 1 - Cards 2',
            'type': 'items',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
        {
            'varname': 'block1_computed_card2',
            'label': 'Block 1 - Computed card 2',
            'type': 'computed',
            'obj': 'carddef:carddef-1',
            'reverse': True,
        },
    ]


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_cards_import_csv(pub, local_user, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

        def post_json_url(url, *args, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.post_json(url, *args, **kwargs)

        def put_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.put(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key), **kwargs
            )

        def post_json_url(url, *args, **kwargs):
            return app.post_json(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key),
                *args,
                **kwargs,
            )

        def put_url(url, **kwargs):
            return app.put(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key), **kwargs
            )

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

    get_app(pub).get(sign_uri('/api/cards/test/import-csv'), status=405)
    get_app(pub).put(sign_uri('/api/cards/test/import-csv'), status=403)
    resp = put_url(
        '/api/cards/test/import-csv',
        params=b'foobar;foobar2\nfirst entry;plop\nsecond entry;plop\n',
        headers={'content-type': 'text/csv'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }

    carddef.id_template = '{{ form_var_foo }}'
    carddef.store()
    carddef.data_class().wipe()
    resp = put_url(
        '/api/cards/test/import-csv',
        params=b'foobar;foobar2\nfirst entry;plop\nsecond entry;plop\n',
        headers={'content-type': 'text/csv'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }

    # async mode
    carddef.data_class().wipe()
    assert carddef.data_class().count() == 0
    resp = put_url(
        '/api/cards/test/import-csv?async=on',
        params=b'foobar;foobar2\nfirst entry;plop\nsecond entry;plop\n',
        headers={'content-type': 'text/csv'},
    )
    # afterjobs are not async in tests: job is already completed during request
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }
    assert resp.json['err'] == 0
    assert 'job' in resp.json['data']
    job_id = resp.json['data']['job']['id']
    assert AfterJob.get(job_id).status == 'completed'
    # get job status from its api url
    job_id = resp.json['data']['job']['id']
    resp = get_url(resp.json['data']['job']['url'])
    assert resp.json['err'] == 0
    assert resp.json['data']['label'] == 'Importing data into cards'
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['creation_time'] <= resp.json['data']['completion_time']
    assert carddef.data_class().select()[0].submission_context == {'method': 'csv_import', 'job_id': job_id}

    # POST
    carddef.data_class().wipe()
    resp = post_json_url(
        '/api/cards/test/import-csv',
        {
            'file': {
                'content': force_str(
                    base64.b64encode(b'foobar;foobar2\nfirst entry;plop\nsecond entry;plop\n')
                )
            }
        },
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}

    resp = post_json_url(
        '/api/cards/test/import-csv',
        {'foo': 'bar'},
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'invalid-format',
        'err_desc': 'Invalid format (must be {"file": {"content": base64}}).',
    }

    resp = post_json_url(
        '/api/cards/test/import-csv',
        {'file': {'content': 'ZZZ'}},
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'invalid-format',
        'err_desc': 'Invalid format (must be {"file": {"content": base64}}).',
    }


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_cards_import_json(pub, local_user, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

        def put_url(url, *args, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.put_json(url, *args, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key), **kwargs
            )

        def put_url(url, *args, **kwargs):
            return app.put_json(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key),
                *args,
                **kwargs,
            )

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

    get_app(pub).get(sign_uri('/api/cards/test/import-json'), status=405)
    get_app(pub).put(sign_uri('/api/cards/test/import-json'), status=403)
    data = {
        'data': [
            {
                'fields': {
                    'foo': 'first entry',
                    'foo2': 'plop',
                }
            },
            {
                'fields': {
                    'foo': 'second entry',
                    'foo2': 'plop',
                }
            },
        ]
    }
    resp = put_url(
        '/api/cards/test/import-json',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }

    carddef.id_template = '{{ form_var_foo }}'
    carddef.store()
    carddef.data_class().wipe()
    resp = put_url(
        '/api/cards/test/import-json',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }

    # json format errors
    resp = put_url(
        '/api/cards/test/import-json',
        {'x': 'y'},
        headers={'content-type': 'application/json'},
        status=400,
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Invalid request',
        'err_code': 'invalid-request',
        'err_desc': 'Invalid JSON file.',
    }

    resp = put_url(
        '/api/cards/test/import-json',
        {'data': [{'x': 'y'}]},
        headers={'content-type': 'application/json'},
        status=400,
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Invalid request',
        'err_code': 'invalid-request',
        'err_desc': 'Invalid JSON file (missing "fields" key on entry).',
    }

    # async mode
    carddef.data_class().wipe()
    assert carddef.data_class().count() == 0
    resp = put_url(
        '/api/cards/test/import-json?async=on',
        data,
        headers={'content-type': 'application/json'},
    )
    # afterjobs are not async in tests: job is already completed during request
    assert carddef.data_class().count() == 2
    assert {x.data['0'] for x in carddef.data_class().select()} == {'first entry', 'second entry'}
    assert {x.digests['default'] for x in carddef.data_class().select()} == {
        'bla first entry xxx',
        'bla second entry xxx',
    }
    assert resp.json['err'] == 0
    assert 'job' in resp.json['data']
    job_id = resp.json['data']['job']['id']
    assert AfterJob.get(job_id).status == 'completed'
    # get job status from its api url
    resp = get_url(resp.json['data']['job']['url'])
    assert resp.json['err'] == 0
    assert resp.json['data']['label'] == 'Importing data into cards'
    assert resp.json['data']['status'] == 'completed'
    assert resp.json['data']['creation_time'] <= resp.json['data']['completion_time']

    # format error in async mode
    LoggedError.wipe()
    carddef.data_class().wipe()
    assert carddef.data_class().count() == 0
    resp = put_url(
        '/api/cards/test/import-json?async=on',
        {'data': [{'x': 'y'}]},
        headers={'content-type': 'application/json'},
    )
    assert LoggedError.count() == 0
    job_id = resp.json['data']['job']['id']
    assert AfterJob.get(job_id).status == 'failed'
    assert AfterJob.get(job_id).failure_label == 'Invalid JSON file (missing "fields" key on entry).'


def test_cards_import_json_update(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    access.roles = [role]
    access.store()

    def put_url(url, *args, **kwargs):
        app.set_authorization(('Basic', ('test', '12345')))
        return app.put_json(url, *args, **kwargs)

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

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo', '1': 'bar'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'0': 'foo2', '1': 'bar2'}
    carddata2.just_created()
    carddata2.store()

    data = {
        'data': [
            {
                'uuid': carddata.uuid,
                'fields': {
                    'foo': 'first entry',
                    'foo2': 'plop',
                },
            },
            {
                'fields': {
                    'foo': 'second entry',
                    'foo2': 'plop',
                }
            },
        ]
    }
    resp = put_url(
        '/api/cards/test/import-json?update-mode=update',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 3
    assert [x.data['0'] for x in carddef.data_class().select(order_by='id')] == [
        'first entry',
        'foo2',
        'second entry',
    ]

    # update mode set to skip
    data['data'][0]['fields']['foo'] = 'on update first entry'
    resp = put_url(
        '/api/cards/test/import-json?update-mode=skip',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 4
    assert [x.data['0'] for x in carddef.data_class().select(order_by='id')] == [
        'first entry',
        'foo2',
        'second entry',
        'second entry',
    ]

    # update mode, invalid value
    put_url(
        '/api/cards/test/import-json?update-mode=xxx',
        data,
        headers={'content-type': 'application/json'},
        status=400,
    )

    # delete mode
    data = {'data': [{'uuid': carddata.uuid, 'fields': {'foo': 'updated entry', 'foo2': 'plop'}}]}
    resp = put_url(
        '/api/cards/test/import-json?delete-mode=keep&update-mode=update',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 4
    assert [x.data['0'] for x in carddef.data_class().select(order_by='id')] == [
        'updated entry',
        'foo2',
        'second entry',
        'second entry',
    ]

    resp = put_url(
        '/api/cards/test/import-json?delete-mode=delete&update-mode=update',
        data,
        headers={'content-type': 'application/json'},
    )
    assert resp.json == {'err': 0}
    assert carddef.data_class().count() == 1
    assert [x.data['0'] for x in carddef.data_class().select(order_by='id')] == ['updated entry']


def test_cards_restricted_api(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foo')]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.store()

    carddef.data_class().wipe()
    formdata = carddef.data_class()()
    formdata.data = {'0': 'blah'}
    formdata.just_created()
    formdata.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    # no role restrictions, get it
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', orig='test', key='12345'))
    assert len(resp.json['data']) == 1

    # restricted to the correct role, get it
    access.roles = [role]
    access.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', orig='test', key='12345'))
    assert len(resp.json['data']) == 1

    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % formdata.id, orig='test', key='12345'))
    assert resp.json['id'] == str(formdata.id)

    # restricted to another role, do not get it
    role2 = pub.role_class(name='second')
    role2.store()
    access.roles = [role2]
    access.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', orig='test', key='12345'), status=403)
    assert resp.json['err_desc'] == 'Unsufficient roles.'

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/%s/' % formdata.id, orig='test', key='12345'), status=403
    )
    assert resp.json['err_desc'] == 'Unsufficient roles.'


def test_cards_http_auth_access(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foo')]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.store()

    carddef.data_class().wipe()
    formdata = carddef.data_class()()
    formdata.data = {'0': 'blah'}
    formdata.just_created()
    formdata.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    # no role restrictions, no admin
    resp = app.get('/api/cards/test/list', status=403)

    # restricted to the correct role, get it
    access.roles = [role]
    access.store()
    resp = app.get('/api/cards/test/list')
    assert len(resp.json['data']) == 1

    # restricted to another role, do not get it
    role2 = pub.role_class(name='second')
    role2.store()
    access.roles = [role2]
    access.store()
    resp = app.get('/api/cards/test/list', status=403)
    assert resp.json['err_desc'] == 'Unsufficient roles.'


def test_card_models_http_auth_access(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    # no role, no access
    app.get('/api/cards/@list', status=403)

    # restricted to role, but with no permissions
    access.roles = [role]
    access.store()
    app.get('/api/cards/@list', status=403)

    # permissions
    pub.cfg['admin-permissions'] = {'cards': [role.id]}
    pub.write_cfg()
    resp = app.get('/api/cards/@list', status=200)
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == 'test'


def test_post_invalid_json(pub, local_user):
    resp = get_app(pub).post(
        '/api/cards/test/submit', params='not a json payload', content_type='application/json', status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'Invalid request'


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_card_submit(pub, local_user, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def post_url(url, *args, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.post(url, *args, **kwargs)

        def post_json_url(url, *args, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.post_json(url, *args, **kwargs)

    else:

        def post_url(url, *args, **kwargs):
            return app.post(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key),
                *args,
                **kwargs,
            )

        def post_json_url(url, *args, **kwargs):
            return app.post_json(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key),
                *args,
                **kwargs,
            )

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar')]
    carddef.store()

    data_class = carddef.data_class()

    resp = get_app(pub).post_json('/api/cards/test/submit', {'data': {}}, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'User not authenticated.'

    resp = post_json_url('/api/cards/test/submit', {'data': {}}, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'User is not allowed to create card.'

    carddef.backoffice_submission_roles = [role.id]
    carddef.store()
    resp = post_json_url('/api/cards/test/submit', {'data': {}})
    assert resp.json['err'] == 0
    assert resp.json['data']['url'] == (
        'http://example.net/backoffice/data/test/%s/' % resp.json['data']['id']
    )
    assert resp.json['data']['backoffice_url'] == (
        'http://example.net/backoffice/data/test/%s/' % resp.json['data']['id']
    )
    assert resp.json['data']['api_url'] == ('http://example.net/api/cards/test/%s/' % resp.json['data']['id'])
    assert data_class.get(resp.json['data']['id']).status == 'wf-recorded'
    if auth == 'signature':
        assert data_class.get(resp.json['data']['id']).user_id == str(local_user.id)
    assert data_class.get(resp.json['data']['id']).tracking_code is None

    local_user2 = get_publisher().user_class()
    local_user2.name = 'Test'
    local_user2.email = 'foo@localhost'
    local_user2.store()
    resp = post_json_url(
        '/api/cards/test/submit', {'data': {}, 'user': {'NameID': [], 'email': local_user2.email}}
    )
    assert data_class.get(resp.json['data']['id']).user.email == local_user2.email

    # bad user format
    resp = post_json_url('/api/cards/test/submit', {'data': {}, 'user': ''}, status=400)
    assert resp.json['err_desc'] == 'Invalid user parameter.'

    resp = post_url(
        '/api/cards/test/submit', json.dumps({'data': {}}), status=400
    )  # missing Content-Type: application/json header
    assert resp.json['err_desc'] == 'Expected JSON but missing appropriate content-type.'

    # check qualified content type are recognized
    resp = post_url(
        '/api/cards/test/submit', json.dumps({'data': {}}), content_type='application/json; charset=utf-8'
    )
    assert resp.json['data']['url']

    # check some invalid content
    resp = post_json_url('/api/cards/test/submit', {'data': None}, status=400)
    resp = post_json_url('/api/cards/test/submit', {'data': 'foobar'}, status=400)
    resp = post_json_url('/api/cards/test/submit', {'data': []}, status=400)
    resp = post_json_url('/api/cards/test/submit', 'datastring', status=400)


def test_carddef_submit_with_varname(pub, local_user):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    source = [{'id': '1', 'text': 'foo', 'more': 'XXX'}, {'id': '2', 'text': 'bar', 'more': 'YYY'}]
    data_source.data_source = {'type': 'jsonvalue', 'value': json.dumps(source)}
    data_source.store()

    data_source = NamedDataSource(name='foobar_jsonp')
    data_source.data_source = {'type': 'jsonp', 'value': 'http://example.com/jsonp'}
    data_source.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar0', varname='foobar0'),
        fields.ItemField(id='1', label='foobar1', varname='foobar1', data_source={'type': 'foobar'}),
        fields.ItemField(id='2', label='foobar2', varname='foobar2', data_source={'type': 'foobar_jsonp'}),
        fields.DateField(id='3', label='foobar3', varname='date'),
        fields.FileField(id='4', label='foobar4', varname='file'),
        fields.MapField(id='5', label='foobar5', varname='map'),
        fields.StringField(id='6', label='foobar6', varname='foobar6'),
    ]
    carddef.backoffice_submission_roles = [role.id]
    carddef.store()
    data_class = carddef.data_class()

    signed_url = sign_url(
        'http://example.net/api/cards/test/submit'
        + '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
        '1234',
    )
    url = signed_url[len('http://example.net') :]
    payload = {
        'data': {
            'foobar0': 'xxx',
            'foobar1': '1',
            'foobar1_structured': {
                'id': '1',
                'text': 'foo',
                'more': 'XXX',
            },
            'foobar2': 'bar',
            'foobar2_raw': '10',
            'date': '1970-01-01',
            'file': {
                'filename': 'test.txt',
                'content': force_str(base64.b64encode(b'test')),
            },
            'map': {
                'lat': 1.5,
                'lon': 2.25,
            },
        }
    }
    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    assert data_class.get(resp.json['data']['id']).status == 'wf-recorded'
    assert data_class.get(resp.json['data']['id']).user_id == str(local_user.id)
    assert data_class.get(resp.json['data']['id']).tracking_code is None
    assert data_class.get(resp.json['data']['id']).data['0'] == 'xxx'
    assert data_class.get(resp.json['data']['id']).data['1'] == '1'
    assert data_class.get(resp.json['data']['id']).data['1_structured'] == source[0]
    assert data_class.get(resp.json['data']['id']).data['2'] == '10'
    assert data_class.get(resp.json['data']['id']).data['2_display'] == 'bar'
    assert data_class.get(resp.json['data']['id']).data['3'] == time.struct_time(
        (1970, 1, 1, 0, 0, 0, 3, 1, -1)
    )

    assert data_class.get(resp.json['data']['id']).data['4'].orig_filename == 'test.txt'
    assert data_class.get(resp.json['data']['id']).data['4'].get_content() == b'test'
    assert data_class.get(resp.json['data']['id']).data['5'] == {'lat': 1.5, 'lon': 2.25}
    # test bijectivity
    assert (
        carddef.fields[3].get_json_value(data_class.get(resp.json['data']['id']).data['3'])
        == payload['data']['date']
    )
    for k in payload['data']['file']:
        data = data_class.get(resp.json['data']['id']).data['4']
        assert carddef.fields[4].get_json_value(data)[k] == payload['data']['file'][k]
    assert (
        carddef.fields[5].get_json_value(data_class.get(resp.json['data']['id']).data['5'])
        == payload['data']['map']
    )


def test_carddef_submit_from_wscall(pub, local_user):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    source = [{'id': '1', 'text': 'foo', 'more': 'XXX'}, {'id': '2', 'text': 'bar', 'more': 'YYY'}]
    data_source.data_source = {'type': 'jsonvalue', 'value': json.dumps(source)}
    data_source.store()

    data_source = NamedDataSource(name='foobar_jsonp')
    data_source.data_source = {'type': 'jsonp', 'value': 'http://example.com/jsonp'}
    data_source.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    local_user2 = get_publisher().user_class()
    local_user2.name = 'Jean Darmette 2'
    local_user2.email = 'jean.darmette2@triffouilis.fr'
    local_user2.name_identifiers = ['0123456789bis']
    local_user2.store()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar0', varname='foobar0'),
        fields.ItemField(id='1', label='foobar1', varname='foobar1', data_source={'type': 'foobar'}),
        fields.ItemField(id='2', label='foobar2', varname='foobar2', data_source={'type': 'foobar_jsonp'}),
        fields.DateField(id='3', label='foobar3', varname='date'),
        fields.FileField(id='4', label='foobar4', varname='file'),
        fields.MapField(id='5', label='foobar5', varname='map'),
        fields.StringField(id='6', label='foobar6', varname='foobar6'),
    ]
    carddef.backoffice_submission_roles = [role.id]
    carddef.workflow = workflow
    carddef.store()

    carddata = carddef.data_class()()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])
    carddata.data = {
        '0': 'xxx',
        '1': '1',
        '1_display': '1',
        '1_structured': {
            'id': '1',
            'text': 'foo',
            'more': 'XXX',
        },
        '2': '10',
        '2_display': 'bar',
        '3': time.strptime('1970-01-01', '%Y-%m-%d'),
        '4': upload,
        '5': {'lat': 1.5, 'lon': 2.25},
        'bo1': 'backoffice field',
    }
    carddata.just_created()
    carddata.store()

    def url():
        signed_url = sign_url(
            'http://example.net/api/cards/test/submit?orig=coucou&email=%s'
            % urllib.parse.quote(local_user.email),
            '1234',
        )
        return signed_url[len('http://example.net') :]

    payload = json.loads(json.dumps(carddata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))

    resp = get_app(pub).post_json(url(), payload)
    assert resp.json['err'] == 0
    new_carddata = carddef.data_class().get(resp.json['data']['id'])
    assert new_carddata.data['0'] == carddata.data['0']
    assert new_carddata.data['1'] == carddata.data['1']
    assert new_carddata.data['1_display'] == carddata.data['1_display']
    assert new_carddata.data['1_structured'] == carddata.data['1_structured']
    assert new_carddata.data['2'] == carddata.data['2']
    assert new_carddata.data['2_display'] == carddata.data['2_display']
    assert new_carddata.data['3'] == carddata.data['3']
    assert new_carddata.data['4'].get_content() == carddata.data['4'].get_content()
    assert new_carddata.data['5'] == carddata.data['5']
    assert new_carddata.data['bo1'] == carddata.data['bo1']
    assert not new_carddata.data.get('6')
    assert new_carddata.user_id == str(local_user.id)

    # add an extra attribute
    payload['extra'] = {'foobar6': 'YYY'}
    resp = get_app(pub).post_json(url(), payload)
    assert resp.json['err'] == 0
    new_carddata = carddef.data_class().get(resp.json['data']['id'])
    assert new_carddata.data['0'] == carddata.data['0']
    assert new_carddata.data['6'] == 'YYY'

    # add user
    carddata.user_id = local_user2.id
    carddata.store()
    payload = json.loads(json.dumps(carddata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))

    resp = get_app(pub).post_json(url(), payload)
    assert resp.json['err'] == 0
    new_carddata = carddef.data_class().get(resp.json['data']['id'])
    assert str(new_carddata.user_id) == str(local_user2.id)

    # test missing map data
    del carddata.data['5']
    payload = json.loads(json.dumps(carddata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))

    resp = get_app(pub).post_json(url(), payload)
    assert resp.json['err'] == 0
    new_carddata = carddef.data_class().get(resp.json['data']['id'])
    assert new_carddata.data.get('5') is None


def test_formdef_submit_structured(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.ItemField(
            id='0',
            label='foobar',
            varname='foobar',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com',
            },
        ),
        fields.ItemField(
            id='1',
            label='foobar1',
            varname='foobar1',
            data_source={
                'type': 'jsonvalue',
                'value': json.dumps([{'id': i, 'text': 'label %s' % i, 'foo': i} for i in range(10)]),
            },
        ),
    ]
    carddef.backoffice_submission_roles = [role.id]
    carddef.store()
    data_class = carddef.data_class()

    for post_data in [
        # straight id
        {'0': '0', '1': '3'},
        # varnames
        {'foobar': '0', 'foobar1': '3'},
        # varnames with integer as values
        {'foobar': 0, 'foobar1': 3},
    ]:
        signed_url = sign_url(
            'http://example.net/api/cards/test/submit'
            '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
            '1234',
        )
        url = signed_url[len('http://example.net') :]

        with responses.RequestsMock() as rsps:
            rsps.get(
                'http://datasource.com',
                json={
                    'data': [
                        {'id': 0, 'text': 'zéro', 'foo': 'bar'},
                        {'id': 1, 'text': 'uné', 'foo': 'bar1'},
                        {'id': 2, 'text': 'deux', 'foo': 'bar2'},
                    ]
                },
            )
            resp = get_app(pub).post_json(url, {'data': post_data})

        formdata = data_class.get(resp.json['data']['id'])
        assert formdata.status == 'wf-recorded'
        assert formdata.data['0'] == '0'
        assert formdata.data['0_display'] == 'zéro'
        assert formdata.data['0_structured'] == {
            'id': 0,
            'text': 'zéro',
            'foo': 'bar',
        }
        assert formdata.data['1'] == '3'
        assert formdata.data['1_display'] == 'label 3'
        assert formdata.data['1_structured'] == {
            'id': 3,
            'text': 'label 3',
            'foo': 3,
        }


def test_card_edit(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    access.roles = [role]
    access.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foobar')]
    carddef.workflow_roles = {'_editor': role.id}
    carddef.store()

    data_class = carddef.data_class()
    carddata = data_class()
    carddata.data = {'0': 'foobar'}
    carddata.just_created()
    carddata.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.post_json(carddata.get_api_url(), {'data': {'foobar': 'baz'}})
    assert resp.json['err'] == 0
    carddata.refresh_from_storage()
    assert carddata.data == {'0': 'baz'}


def test_card_parent_form_url(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {}
    carddata.just_created()
    carddata.submission_context = {
        'object_type': 'formdef',
        'orig_formdef_id': formdef.id,
        'orig_formdata_id': formdata.id,
    }
    carddata.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.get(carddata.get_api_url())
    assert resp.json['submission']['parent'] == {
        'url': 'http://example.net/test/%s/' % formdata.id,
        'backoffice_url': 'http://example.net/backoffice/management/test/%s/' % formdata.id,
        'api_url': 'http://example.net/api/forms/test/%s/' % formdata.id,
    }


def test_cards_filter_function(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.name_identifiers = ['0123456789']
    local_user.store()

    user1 = pub.user_class(name='userA')
    user1.name_identifiers = ['56789']
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.name_identifiers = ['98765']
    user2.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = []
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.digest_templates = {'default': 'bla {{ form_number }} xxx'}
    carddef.store()

    carddef.data_class().wipe()

    carddatas = []
    for i in range(3):
        carddatas.append(carddef.data_class()())

    carddatas[0].workflow_roles = {'_foobar': [str(role.id)]}
    carddatas[1].workflow_roles = {'_foobar': ['_user:%s' % user1.id]}
    carddatas[2].workflow_roles = {'_foobar': ['_user:%s' % user1.id, '_user:%s' % user2.id]}

    for carddata in carddatas:
        carddata.just_created()
        carddata.jump_status('recorded')
        carddata.store()

    # no paramater, -> get everything
    resp = get_app(pub).get(sign_uri('/api/cards/test/list'))
    assert len(resp.json['data']) == 3

    # filter on missing uuid
    resp = get_app(pub).get(
        sign_uri('/api/cards/test/list?filter-user-function=_foobar&filter-user-uuid=XXX')
    )
    assert len(resp.json['data']) == 0

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list?filter-user-function=_foobar&filter-user-uuid=%s'
            % user1.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 2

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list?filter-user-function=_foobar&filter-user-uuid=%s'
            % user2.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 1

    # filter on empty value
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?filter-user-function=on'))
    assert len(resp.json['data']) == 0

    # filter on role
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list?filter-user-function=_foobar&filter-user-uuid=%s'
            % local_user.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 1

    # via custom view
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-user-function': 'on', 'filter-user-function-value': '_foobar'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/shared-carddef-custom-view?filter-user-uuid=%s' % user1.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 2

    # make sure formdata functions override formdef functions
    carddef.workflow_roles = {'_foobar': role.id}
    carddef.store()
    carddef.data_class().rebuild_security()

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/shared-carddef-custom-view?filter-user-uuid=%s'
            % local_user.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 1

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list/shared-carddef-custom-view?filter-user-uuid=%s' % user1.name_identifiers[0]
        )
    )
    assert len(resp.json['data']) == 2
