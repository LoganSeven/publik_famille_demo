import json
import os

import pytest
from quixote import get_publisher

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.qommon.http_request import HTTPRequest

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

    pub.user_class.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_api_filter_options_item_field_datasource(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    values = [{'id': '9', 'text': 'foo'}, {'id': '10', 'text': 'bar'}, {'id': '11', 'text': 'baz'}]
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(values),
    }
    data_source.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='1', label='String', varname='item'),  # must be ignored
        fields.ItemField(id='0', label='Item', data_source={'type': 'foobar'}, varname='item'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': str(9 + i),
            '0_display': {v['id']: v['text'] for v in values}.get(str(9 + i)),
        }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': '',
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=item', user=local_user))
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [{'id': '10', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '11', 'text': 'baz'}, {'id': '9', 'text': 'foo'}]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '9', 'text': 'foo'}]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]


def test_api_filter_options_item_field_items(pub, local_user):
    # with items: returns items, don't look in DB
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemField(id='0', label='Item', items=['foo', 'bar'], varname='item'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': 'foo' if i % 2 else 'bar',
            '0_display': 'foo' if i % 2 else 'bar',
        }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': '',
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=item', user=local_user))
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]


def test_api_filter_options_item_field_carddef(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:foo'}, varname='item'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': str(i + 1),
                '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': '',
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=item', user=local_user))
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '3', 'text': 'baz'}, {'id': '1', 'text': 'foo'}]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '1', 'text': 'foo'}]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]


def test_api_filter_options_item_field_carddef_and_customview(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:foo'}, varname='item'),
        fields.BoolField(id='1', label='Bool', varname='bool'),
    ]
    carddef.store()
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'true'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': str(i + 1),
                '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': '',
            }
        carddata.data['1'] = bool(i)
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options/custom-view?filter_field_id=item', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=item&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '3', 'text': 'baz'}]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == []
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
    ]


def test_api_filter_options_items_field_datasource(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    values = [{'id': '9', 'text': 'foo'}, {'id': '10', 'text': 'bar'}, {'id': '11', 'text': 'baz'}]
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(values),
    }
    data_source.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemsField(id='0', label='Items', data_source={'type': 'foobar'}, varname='items'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': [str(9 + i)],
            '0_display': {v['id']: v['text'] for v in values}.get(str(9 + i)),
        }
        if i == 0:
            carddata.data = {
                '0': ['9', '10'],
                '0_display': 'foo, bar',
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': [],
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=items', user=local_user))
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [{'id': '10', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '9', 'text': 'foo'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]


def test_api_filter_options_items_field_items(pub, local_user):
    # with items: returns items, don't look in DB
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemsField(id='0', label='Items', items=['foo', 'bar'], varname='items'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': ['foo' if i % 2 else 'bar'],
            '0_display': 'foo' if i % 2 else 'bar',
        }
        if i == 0:
            carddata.data = {
                '0': ['foo', 'bar'],
                '0_display': 'foo, bar',
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': [],
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=items', user=local_user))
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]


def test_api_filter_options_items_field_carddef(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemsField(id='0', label='Items', data_source={'type': 'carddef:foo'}, varname='items'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': [str(i + 1)],
                '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
            }
        if i == 0:
            carddata.data = {
                '0': ['1', '2'],
                '0_display': 'card 1, card 2',
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': [],
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/filter-options?filter_field_id=items', user=local_user))
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-user-uuid=ABCDEF', user=local_user
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '1', 'text': 'foo'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=items&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]


def test_api_filter_options_items_field_carddef_and_customview(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemsField(id='0', label='Items', data_source={'type': 'carddef:foo'}, varname='items'),
        fields.BoolField(id='1', label='Bool', varname='bool'),
    ]
    carddef.store()
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'true'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': [str(i + 1)],
                '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
            }
        if i == 0:
            carddata.data = {
                '0': ['1', '2'],
                '0_display': 'card foo, card bar',
            }
        carddata.data['1'] = bool(i)
        if i == 3:
            # Empty values
            carddata.data = {
                '0': [],
                '1': True,
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options/custom-view?filter_field_id=items', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=items&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=items&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '3', 'text': 'baz'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=items&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == []
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=items&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
    ]


def test_api_filter_options_block_item_field_datasource(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    values = [{'id': '9', 'text': 'foo'}, {'id': '10', 'text': 'bar'}, {'id': '11', 'text': 'baz'}]
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(values),
    }
    data_source.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'foobar'}, varname='item'),
    ]
    block.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': {
                'data': [
                    {
                        '0': str(9 + i),
                        '0_display': {v['id']: v['text'] for v in values}.get(str(9 + i)),
                    },
                ],
                'schema': {},  # not important here
            },
            '0_display': 'hello',
        }
        if i == 0:
            carddata.data['0']['data'].append(
                {
                    '0': '10',
                    '0_display': 'bar',
                },
            )
        if i == 1:
            # 2 elements, the second without values
            carddata.data['0']['data'].append(
                {
                    '0': '',
                }
            )
        if i == 2:
            # 2 elements, the second with non values
            carddata.data['0']['data'].append({})
        if i == 3:
            # only one element, without values
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': '',
                        }
                    ]
                }
            }
        if i == 4:
            # no element
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        if i == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options?filter_field_id=blockdata_item', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '10', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '9', 'text': 'foo'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '10', 'text': 'bar'},
        {'id': '11', 'text': 'baz'},
        {'id': '9', 'text': 'foo'},
    ]


def test_api_filter_options_block_item_field_items(pub, local_user):
    # with items: returns items, don't look in DB
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='0', label='Item', items=['foo', 'bar'], varname='item'),
    ]
    block.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        carddata.data = {
            '0': {
                'data': [
                    {
                        '0': 'foo' if i % 2 else 'bar',
                        '0_display': 'foo' if i % 2 else 'bar',
                    },
                ],
                'schema': {},  # not important here
            },
            '0_display': 'hello',
        }
        if i == 0:
            carddata.data['0']['data'].append(
                {
                    '0': 'bar',
                    '0_display': 'bar',
                },
            )
        if i == 1:
            # 2 elements, the second without values
            carddata.data['0']['data'].append(
                {
                    '0': '',
                }
            )
        if i == 2:
            # 2 elements, the second with non values
            carddata.data['0']['data'].append({})
        if i == 3:
            # only one element, without values
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': '',
                        }
                    ]
                }
            }
        if i == 4:
            # no element
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options?filter_field_id=blockdata_item', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': 'foo', 'text': 'foo'},
        {'id': 'bar', 'text': 'bar'},
    ]


def test_api_filter_options_block_item_field_carddef(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:foo'}, varname='item'),
    ]
    block.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': str(i + 1),
                            '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
                        }
                    ],
                    'schema': {},  # not important here
                },
                '0_display': 'hello',
            }
        if i == 0:
            carddata.data['0']['data'].append(
                {
                    '0': '2',
                    '0_display': 'bar',
                },
            )
        if i == 1:
            # 2 elements, the second without values
            carddata.data['0']['data'].append(
                {
                    '0': '',
                }
            )
        if i == 2:
            # 2 elements, the second with non values
            carddata.data['0']['data'].append({})
        if i == 3:
            # only one element, without values
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': '',
                        }
                    ]
                }
            }
        if i == 4:
            # no element
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options?filter_field_id=blockdata_item', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '1', 'text': 'foo'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options?filter_field_id=blockdata_item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]


def test_api_filter_options_block_item_field_carddef_and_customview(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:foo'}, varname='item'),
        fields.BoolField(id='1', label='Bool', varname='bool'),
    ]
    block.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    carddef.store()
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-0-1': 'on', 'filter-0-1-value': 'true'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': str(i + 1),
                            '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
                            '1': bool(i),
                        }
                    ],
                    'schema': {},  # not important here
                },
                '0_display': 'hello',
            }
        if i == 0:
            carddata.data['0']['data'].append(
                {
                    '0': '2',
                    '0_display': 'bar',
                    '1': True,
                },
            )
        if i == 1:
            # 2 elements, the second without values
            carddata.data['0']['data'].append(
                {
                    '0': '',
                }
            )
        if i == 2:
            # 2 elements, the second with non values
            carddata.data['0']['data'].append({})
        if i == 3:
            # only one element, without values
            carddata.data = {
                '0': {
                    'data': [
                        {
                            '0': '',
                        }
                    ]
                }
            }
        if i == 4:
            # no element
            carddata.data = {}
        carddata.user_id = None
        if i == 0:
            carddata.user_id = local_user.id
        if i == 1:
            carddata.user_id = another_user.id
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status('deleted')
        else:
            carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/filter-options/custom-view?filter_field_id=blockdata_item', user=local_user)
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on user uuid
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=blockdata_item&filter-user-uuid=ABCDEF',
            user=local_user,
        )
    )
    assert resp.json['data'] == [{'id': '2', 'text': 'bar'}]

    # filter on ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=blockdata_item&filter-identifier=%s'
            % ','.join(['1', '3', '4', '5']),
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]

    # filter on status
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=blockdata_item&filter=deleted&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '1', 'text': 'foo'},
    ]
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/filter-options/custom-view?filter_field_id=blockdata_item&filter=deleted|recorded&filter-operator=in',
            user=local_user,
        )
    )
    assert resp.json['data'] == [
        {'id': '2', 'text': 'bar'},
        {'id': '3', 'text': 'baz'},
        {'id': '1', 'text': 'foo'},
    ]


# no filtering on items field in a block field
