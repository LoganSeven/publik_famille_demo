import datetime
import json
import os

import pytest
from django.utils.timezone import make_aware

from wcs import fields
from wcs.backoffice.management import format_time
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
from .utils import sign_uri


def get_humanized_duration_serie(json_resp):
    return [format_time(x) for x in json_resp['data']['series'][0]['data']]


def get_humanized_duration_series(json_resp):
    results = []
    for serie in json_resp['data']['series']:
        results.append((serie['label'], [format_time(x) for x in serie['data']]))
    return results


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    BlockDef.wipe()
    Category.wipe()
    FormDef.wipe()
    Workflow.wipe()
    CardDef.wipe()

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


@pytest.fixture
def formdef(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    middle_status1 = workflow.add_status(name='Middle status 1')
    middle_status2 = workflow.add_status(name='Middle status 2')
    just_submitted_status = workflow.add_status(name='Just submitted', id='just_submitted')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    jump.timeout = 86400
    jump.mode = 'timeout'
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '3'
    jump = middle_status1.add_action('jump', id='_jump')
    jump.status = '4'
    jump = middle_status2.add_action('jump', id='_jump')
    jump.status = '2'
    jump = just_submitted_status.add_action('jump', id='_jump')
    jump.status = '1'
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.BoolField(id='1', varname='checkbox', label='Checkbox', display_locations=['statistics']),
    ]
    workflow.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.BoolField(id='1', label='Bool', varname='bool', display_locations=['statistics']),
        fields.ItemsField(
            id='2',
            varname='block-items',
            label='Block items',
            items=['Foo', 'Bar', 'Baz'],
            anonymise='no',
            display_locations=['statistics'],
        ),
    ]
    block.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': 'foo', 'text': 'Foo'}, {'id': 'bar', 'text': 'Bar'}, {'id': 'baz', 'text': 'Baz'}]
        ),
    }

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    item_field = fields.ItemField(id='2', varname='test-item', label='Test item', data_source=data_source)
    item_field.display_locations = ['statistics']
    items_field = fields.ItemsField(
        id='3',
        varname='test-items',
        label='Test items',
        data_source=data_source,
        anonymise='no',
    )
    items_field.display_locations = ['statistics']
    block_field = fields.BlockField(
        id='4', label='Block Data', varname='blockdata', block_slug='foobar', anonymise='no'
    )
    formdef.fields = [item_field, items_field, block_field]
    formdef.store()
    formdef.data_class().wipe()
    return formdef


def teardown_module(module):
    clean_temporary_pub()


def test_statistics_index(pub):
    get_app(pub).get('/api/statistics/', status=403)
    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    assert resp.json['data'][0]['name'] == 'Forms Count'
    assert resp.json['data'][0]['url'] == 'http://example.net/api/statistics/forms/count/'


def test_statistics_index_forms(pub):
    formdef = FormDef()
    formdef.name = 'test 1'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.fields = []
    formdef2.store()
    formdef2.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    form_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [
        {'id': 'test-1', 'label': 'test 1'},
        {'id': 'test-2', 'label': 'test 2'},
    ]

    category_a = Category(name='Category A')
    category_a.store()
    category_b = Category(name='Category B')
    category_b.store()
    formdef2.category_id = category_a.id
    formdef2.store()

    formdef3 = FormDef()
    formdef3.name = 'test 3'
    formdef3.category_id = category_b.id
    formdef3.store()
    formdef3.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    form_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [
        [
            'Category A',
            [
                {'id': 'category:category-a', 'label': 'All forms of category Category A'},
                {'id': 'test-2', 'label': 'test 2'},
            ],
        ],
        [
            'Category B',
            [
                {'id': 'category:category-b', 'label': 'All forms of category Category B'},
                {'id': 'test-3', 'label': 'test 3'},
            ],
        ],
        ['Misc', [{'id': 'test-1', 'label': 'test 1'}]],
    ]

    # check Misc is not shown if all forms have categories
    formdef.category_id = category_a.id
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    form_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [
        [
            'Category A',
            [
                {'id': 'category:category-a', 'label': 'All forms of category Category A'},
                {'id': 'test-1', 'label': 'test 1'},
                {'id': 'test-2', 'label': 'test 2'},
            ],
        ],
        [
            'Category B',
            [
                {'id': 'category:category-b', 'label': 'All forms of category Category B'},
                {'id': 'test-3', 'label': 'test 3'},
            ],
        ],
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    form_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'channel'][0]
    assert form_filter['options'] == [
        {'id': '_all', 'label': 'All'},
        {'id': 'backoffice', 'label': 'Backoffice'},
        {'id': 'mail', 'label': 'Mail'},
        {'id': 'email', 'label': 'Email'},
        {'id': 'phone', 'label': 'Phone'},
        {'id': 'counter', 'label': 'Counter'},
        {'id': 'fax', 'label': 'Fax'},
        {'id': 'web', 'label': 'Web'},
        {'id': 'social-network', 'label': 'Social Network'},
    ]


def test_statistics_index_cards(pub):
    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    assert len([x for x in resp.json['data'] if x['id'] == 'cards_counts']) == 0

    carddef = CardDef()
    carddef.name = 'test 1'
    carddef.fields = []
    carddef.store()
    carddef.data_class().wipe()

    carddef2 = CardDef()
    carddef2.name = 'test 2'
    carddef2.fields = []
    carddef2.store()
    carddef2.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    assert len([x for x in resp.json['data'] if x['id'] == 'cards_counts']) == 1

    form_filter = [x for x in resp.json['data'][1]['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [
        {'id': 'test-1', 'label': 'test 1'},
        {'id': 'test-2', 'label': 'test 2'},
    ]

    category_a = CardDefCategory(name='Category A')
    category_a.store()
    category_b = CardDefCategory(name='Category B')
    category_b.store()
    carddef2.category_id = category_a.id
    carddef2.store()

    carddef3 = CardDef()
    carddef3.name = 'test 3'
    carddef3.category_id = category_b.id
    carddef3.store()
    carddef3.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    form_filter = [x for x in resp.json['data'][1]['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [
        ['Category A', [{'id': 'test-2', 'label': 'test 2'}]],
        ['Category B', [{'id': 'test-3', 'label': 'test 3'}]],
        ['Misc', [{'id': 'test-1', 'label': 'test 1'}]],
    ]


def test_statistics_index_resolution_time(pub):
    formdef = FormDef()
    formdef.name = 'test 1'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    resolution_time_stat = [x for x in resp.json['data'] if x['id'] == 'resolution_time'][0]
    form_filter = [x for x in resolution_time_stat['filters'] if x['id'] == 'form'][0]
    assert form_filter['options'] == [{'id': 'test-1', 'label': 'test 1'}]


def test_statistics_index_resolution_time_cards(pub):
    carddef = CardDef()
    carddef.name = 'test 1'
    carddef.fields = []
    carddef.store()
    carddef.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/statistics/'))
    resolution_time_stat = [x for x in resp.json['data'] if x['id'] == 'resolution_time_cards'][0]
    card_filter = [x for x in resolution_time_stat['filters'] if x['id'] == 'form'][0]
    assert card_filter['options'] == [{'id': 'test-1', 'label': 'test 1'}]


def test_statistics_forms_count(pub):
    category_a = Category(name='Category A')
    category_a.store()
    category_b = Category(name='Category B')
    category_b.store()

    formdef = FormDef()
    formdef.name = 'test 1'
    formdef.category_id = category_a.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.category_id = category_b.id
    formdef2.fields = []
    formdef2.store()
    formdef2.data_class().wipe()

    for i in range(20):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        # "Web" channel has three equivalent values
        if i == 0:
            formdata.submission_channel = 'web'
        elif i == 1:
            formdata.submission_channel = ''
        else:
            formdata.submission_channel = None
            formdata.backoffice_submission = bool(i % 3 == 0)
        formdata.store()

    for i in range(30):
        formdata = formdef2.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
        formdata.backoffice_submission = bool(i % 3)
        formdata.submission_channel = 'mail'
        formdata.store()

    # draft should not be counted
    formdata = formdef.data_class()()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
    formdata.status = 'draft'
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/'))
    assert resp.json['data']['series'] == [{'data': [20, 0, 30], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=year'))
    assert resp.json['data']['series'] == [{'data': [50], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021']

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=weekday'))
    assert resp.json['data']['series'] == [{'data': [30, 0, 0, 0, 20, 0, 0], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == [
        'Monday',
        'Tuesday',
        'Wednesday',
        'Thursday',
        'Friday',
        'Saturday',
        'Sunday',
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=hour'))
    assert resp.json['data']['series'] == [
        {
            'data': [20, 0, 30, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            'label': 'Forms Count',
        }
    ]
    assert resp.json['data']['x_labels'] == list(range(24))

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=none'))
    assert resp.json['data']['series'] == [{'data': [50], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['']

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=day'))
    assert resp.json['data']['series'] == [{'data': [20, 30], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01-01', '2021-03-01']

    # apply category filter through form parameter
    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?form=category:category-a'))
    assert resp.json['data']['series'] == [{'data': [20], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01']

    # apply form filter
    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?form=%s' % formdef.url_name))
    assert resp.json['data']['series'] == [{'data': [20], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01']

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?form=%s' % 'invalid'), status=404)

    # apply period filter
    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?end=2021-02-01'))
    assert resp.json['data']['series'] == [{'data': [20], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01']

    # apply channel filter
    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?channel=mail'))
    assert resp.json['data']['series'] == [{'data': [30], 'label': 'Forms Count'}]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?channel=web'))
    assert resp.json['data']['series'] == [{'data': [14], 'label': 'Forms Count'}]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?channel=backoffice'))
    assert resp.json['data']['series'] == [{'data': [6], 'label': 'Forms Count'}]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?channel=_all'))
    assert resp.json['data']['series'] == [{'data': [20, 0, 30], 'label': 'Forms Count'}]


def test_statistics_forms_count_subfilters(pub, formdef):
    for i in range(2):
        formdata = formdef.data_class()()
        formdata.data['2'] = 'foo' if i % 2 else 'baz'
        formdata.data['2_display'] = 'Foo' if i % 2 else 'Baz'
        formdata.data['3'] = ['foo'] if i % 2 else ['bar', 'baz']
        formdata.data['3_display'] = 'Foo' if i % 2 else 'Bar, Baz'
        formdata.data['4'] = {'data': [{'2': ['foo', 'bar'], '2_display': 'Foo, Bar'}]}
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    url = '/api/statistics/forms/count/?form=%s&time_interval=year&include-subfilters=true' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url))

    # check group-by subfilter
    assert resp.json['data']['subfilters'][0] == {
        'id': 'group-by',
        'label': 'Group by',
        'options': [
            {'id': 'channel', 'label': 'Channel'},
            {'id': 'simple-status', 'label': 'Simplified status'},
            {'id': 'test-item', 'label': 'Test item'},
            {'id': 'test-items', 'label': 'Test items'},
            {'id': 'blockdata_bool', 'label': 'Bool'},
            {'id': 'blockdata_block-items', 'label': 'Block items'},
            {'id': 'checkbox', 'label': 'Checkbox'},
            {'id': 'status', 'label': 'Status'},
        ],
        'has_subfilters': True,
    }

    # check item field subfilter
    assert resp.json['data']['subfilters'][1] == {
        'id': 'filter-test-item',
        'label': 'Test item',
        'options': [{'id': 'baz', 'label': 'Baz'}, {'id': 'foo', 'label': 'Foo'}],
        'required': False,
    }

    # check items field subfilter
    assert resp.json['data']['subfilters'][2] == {
        'id': 'filter-test-items',
        'label': 'Test items',
        'options': [
            {'id': 'bar', 'label': 'Bar'},
            {'id': 'baz', 'label': 'Baz'},
            {'id': 'foo', 'label': 'Foo'},
        ],
        'required': False,
    }

    # check block boolean field subfilter
    assert resp.json['data']['subfilters'][3] == {
        'id': 'filter-blockdata_bool',
        'label': 'Bool',
        'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
        'required': False,
    }

    # check block items field subfilter
    assert resp.json['data']['subfilters'][4] == {
        'id': 'filter-blockdata_block-items',
        'label': 'Block items',
        'options': [
            {'id': 'Foo', 'label': 'Foo'},
            {'id': 'Bar', 'label': 'Bar'},
            {'id': 'Baz', 'label': 'Baz'},
        ],
        'required': False,
    }

    # check boolean backoffice field subfilter
    assert resp.json['data']['subfilters'][5] == {
        'id': 'filter-checkbox',
        'label': 'Checkbox',
        'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
        'required': False,
    }

    # check status subfilter
    assert resp.json['data']['subfilters'][-1] == {
        'default': '_all',
        'id': 'filter-status',
        'label': 'Status',
        'options': [
            {'id': '_all', 'label': 'All'},
            {'id': 'pending', 'label': 'Open'},
            {'id': 'done', 'label': 'Done'},
            {'id': '1', 'label': 'New status'},
            {'id': '2', 'label': 'End status'},
        ],
        'required': True,
    }

    # group by triggers new subfilter
    new_resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert new_resp.json['data']['subfilters'][1] == {
        'id': 'hide_none_label',
        'label': 'Ignore forms where "Test item" is empty.',
        'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
        'required': True,
        'default': 'false',
    }
    assert len(new_resp.json['data']['subfilters']) == len(resp.json['data']['subfilters']) + 1

    # month time_interval triggers new subfilter
    new_resp = get_app(pub).get(sign_uri(url.replace('year', 'month')))
    assert new_resp.json['data']['subfilters'][0] == {
        'id': 'months_to_show',
        'label': 'Number of months to show',
        'options': [
            {'id': '_all', 'label': 'All'},
            {'id': '6', 'label': 'Last six months'},
            {'id': '12', 'label': 'Last twelve months'},
        ],
        'required': True,
        'default': '_all',
    }
    assert len(new_resp.json['data']['subfilters']) == len(resp.json['data']['subfilters']) + 1

    # add item field with datasource and no formdata, it should not appear
    item_field = fields.ItemField(
        id='20',
        varname='test-item-no-formdata',
        label='Test item no formdata',
        data_source={
            'type': 'jsonvalue',
            'value': json.dumps(
                [{'id': 'foo', 'text': 'Foo'}, {'id': 'bar', 'text': 'Bar'}, {'id': 'baz', 'text': 'Baz'}]
            ),
        },
        display_locations=['statistics'],
    )
    formdef.fields.append(item_field)
    formdef.store()
    new_resp = get_app(pub).get(sign_uri(url))
    assert new_resp.json == resp.json

    # add boolean field with no varname, it should not appear
    bool_field = fields.BoolField(id='21', label='Checkbox', display_locations=['statistics'])
    formdef.fields.append(bool_field)
    formdef.store()
    new_resp = get_app(pub).get(sign_uri(url))
    assert new_resp.json == resp.json

    # add boolean field with no display location, it should not appear
    bool_field = fields.BoolField(
        id='22', varname='checkbox', label='Checkbox', display_locations=['validation']
    )
    formdef.fields.append(bool_field)
    formdef.store()
    new_resp = get_app(pub).get(sign_uri(url))
    assert new_resp.json == resp.json

    # add not filterable field, it should not appear
    formdef.fields.append(fields.StringField(id='23', varname='test string', label='Test'))
    formdef.store()
    new_resp = get_app(pub).get(sign_uri(url))
    assert new_resp.json == resp.json

    # remove fields and statuses
    workflow = Workflow(name='Empty wf')
    workflow.add_status('New')
    workflow.store()
    formdef.workflow = workflow
    formdef.fields.clear()
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get(sign_uri(url))
    assert resp.json['data'] == {
        'series': [{'data': [], 'label': 'Forms Count'}],
        'x_labels': [],
        'subfilters': [
            {
                'has_subfilters': True,
                'id': 'group-by',
                'label': 'Group by',
                'options': [
                    {'id': 'channel', 'label': 'Channel'},
                    {'id': 'simple-status', 'label': 'Simplified status'},
                    {'id': 'status', 'label': 'Status'},
                ],
            },
            {
                'default': '_all',
                'id': 'filter-status',
                'label': 'Status',
                'options': [
                    {'id': '_all', 'label': 'All'},
                    {'id': 'pending', 'label': 'Open'},
                    {'id': 'done', 'label': 'Done'},
                    {'id': '1', 'label': 'New'},
                ],
                'required': True,
            },
        ],
    }


def test_statistics_forms_count_subfilters_empty_block_items_field(pub, formdef):
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.store()

    formdef.fields[2].block.fields[1].data_source = {'type': 'carddef:foo'}
    formdef.fields[2].block.store()

    formdata = formdef.data_class()()
    formdata.data['4'] = {'data': [{'1': 'a', '1_display': 'B'}]}
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.store()

    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?form=%s&include-subfilters=true' % formdef.url_name)
    )

    # check block items field subfilter
    assert not any(x['id'] == 'filter-blockdata_block-items' for x in resp.json['data']['subfilters'])


def test_statistics_forms_count_subfilters_empty_item_field_no_datasource(pub, formdef):
    formdef.workflow.backoffice_fields_formdef.fields.append(
        fields.ItemField(
            id='10',
            varname='empty-item-field',
            label='Empty item field',
            anonymise='no',
            display_locations=['statistics'],
        ),
    )
    formdef.workflow.store()
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['10'] = 'extra-option'
    formdata.data['10_display'] = 'Extra option'
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.store()

    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?form=%s&include-subfilters=true' % formdef.url_name)
    )
    filter_dict = [x for x in resp.json['data']['subfilters'] if x['id'] == 'filter-empty-item-field'][0]
    assert filter_dict['options'] == [{'id': 'extra-option', 'label': 'Extra option'}]


def test_statistics_forms_count_subfilters_card_options(pub, formdef):
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [fields.StringField(id='0', label='Name', varname='name')]
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.store()

    carddata1 = carddef.data_class()()
    carddata1.just_created()
    carddata1.data['0'] = 'xxx'
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.just_created()
    carddata2.data['0'] = 'yyy'
    carddata2.store()

    carddata3 = carddef.data_class()()
    carddata3.just_created()
    carddata3.data['0'] = 'zzz'
    carddata3.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='Bar',
            varname='bar',
            data_source={'type': 'carddef:foo'},
            display_locations=['statistics'],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = str(carddata1.id)
    formdata.data['1_display'] = carddata1.get_display_label()
    formdata.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = str(carddata2.id)
    formdata.data['1_display'] = carddata2.get_display_label()
    formdata.store()

    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?form=%s&include-subfilters=true' % formdef.url_name)
    )

    item_subfilter = [x for x in resp.json['data']['subfilters'] if x['id'] == 'filter-bar'][0]
    assert item_subfilter['options'] == [
        {'id': '1', 'label': 'xxx'},
        {'id': '2', 'label': 'yyy'},
    ]

    # option list is not affected by card deletion
    carddata2.remove_self()
    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?form=%s&include-subfilters=true' % formdef.url_name)
    )

    item_subfilter = [x for x in resp.json['data']['subfilters'] if x['id'] == 'filter-bar'][0]
    assert item_subfilter['options'] == [
        {'id': '1', 'label': 'xxx'},
        {'id': '2', 'label': 'yyy'},
    ]


def test_statistics_forms_count_subfilters_query(pub, formdef):
    for i in range(20):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i % 3:
            formdata.data['1'] = True
            formdata.data['2'] = 'foo'
            formdata.data['3'] = ['bar', 'baz']
            formdata.data['4'] = {
                'data': [
                    {'1': True, '2': ['baz'], '2_display': 'Baz'},
                    {'1': False, '2': ['foo'], '2_display': 'Foo'},
                ]
            }
        elif i % 2:
            formdata.data['1'] = False
            formdata.data['2'] = 'baz'
            formdata.data['3'] = ['baz']
            formdata.data['4'] = {'data': [{'1': False, '2': ['foo', 'bar'], '2_display': 'Foo, Bar'}]}
            formdata.jump_status('2')
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    # query all formdata
    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url))
    assert resp.json['data']['series'][0]['data'][0] == 20

    # filter on boolean field
    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=true'))
    assert resp.json['data']['series'][0]['data'][0] == 13

    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=false'))
    assert resp.json['data']['series'][0]['data'][0] == 3

    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox='), status=400)
    assert resp.text == 'Invalid value "" for "filter-checkbox".'

    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=xxx'), status=400)
    assert resp.text == 'Invalid value "xxx" for "filter-checkbox".'

    # filter on item field
    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=foo'))
    assert resp.json['data']['series'][0]['data'][0] == 13

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=baz'))
    assert resp.json['data']['series'][0]['data'][0] == 3

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=bar'))
    assert resp.json['data']['series'][0]['data'] == []

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item='))
    assert resp.json['data']['series'][0]['data'] == []

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=xxx'))
    assert resp.json['data']['series'][0]['data'] == []

    # filter on item field, with a carddef datasource
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.store()

    formdef.fields[0].data_source = {'type': 'carddef:foo'}
    formdef.store()

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=xxx'))
    assert resp.json['data']['series'][0]['data'] == []
    assert LoggedError.count() == 0

    # filter on items field
    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=foo'))
    assert resp.json['data']['series'][0]['data'] == []

    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=bar'))
    assert resp.json['data']['series'][0]['data'][0] == 13

    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=baz'))
    assert resp.json['data']['series'][0]['data'][0] == 16

    # filter on block boolean field
    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_bool=true'))
    assert resp.json['data']['series'][0]['data'][0] == 13

    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_bool=false'))
    assert resp.json['data']['series'][0]['data'][0] == 16

    # filter on block items field
    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_block-items=foo'))
    assert resp.json['data']['series'][0]['data'][0] == 16

    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_block-items=bar'))
    assert resp.json['data']['series'][0]['data'][0] == 3

    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_block-items=baz'))
    assert resp.json['data']['series'][0]['data'][0] == 13

    # filter on status
    resp = get_app(pub).get(sign_uri(url + '&filter-status=_all'))
    assert resp.json['data']['series'][0]['data'][0] == 20

    resp = get_app(pub).get(sign_uri(url + '&filter-status=1'))
    assert resp.json['data']['series'][0]['data'][0] == 17

    resp = get_app(pub).get(sign_uri(url + '&filter-status=pending'))
    assert resp.json['data']['series'][0]['data'][0] == 17

    resp = get_app(pub).get(sign_uri(url + '&filter-status=2'))
    assert resp.json['data']['series'][0]['data'][0] == 3

    resp = get_app(pub).get(sign_uri(url + '&filter-status=done'))
    assert resp.json['data']['series'][0]['data'][0] == 3

    resp = get_app(pub).get(sign_uri(url + '&filter-status='))
    assert resp.json['data']['series'][0]['data'][0] == 20

    resp = get_app(pub).get(sign_uri(url + '&filter-status=xxx'))
    assert resp.json['data']['series'][0]['data'][0] == 20

    # invalid filter
    resp = get_app(pub).get(sign_uri(url + '&filter-xxx=yyy'))
    assert resp.json['data']['series'][0]['data'][0] == 20


def test_statistics_forms_count_subfilters_query_same_varname(pub, formdef):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(id='1', varname='test', label='Test', items=['foo', 'bar']),
        fields.ItemField(
            id='2',
            varname='test',
            label='Test',
            items=['foo', 'bar'],
            display_locations=['statistics'],
        ),
    ]
    formdef.store()

    formdatas = []
    for i in range(5):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        if i == 0:
            formdata.data['1'] = 'foo'
        if i == 1:
            formdata.data['1'] = 'bar'
        formdata.data['2'] = 'foo'
        formdata.store()
        formdatas.append(formdata)

    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url + '&filter-test=foo'))
    assert resp.json['data']['series'] == [{'data': [5], 'label': 'Forms Count'}]

    formdef.fields[0].display_locations = ['statistics']
    formdef.store()
    for formdata in formdatas:
        formdata.store()  # refresh statistics_data column

    # first non empty value is used : 4 are 'foo' and one is 'bar' hence 4 results
    resp = get_app(pub).get(sign_uri(url + '&filter-test=foo'))
    assert resp.json['data']['series'] == [{'data': [4], 'label': 'Forms Count'}]

    resp = get_app(pub).get(sign_uri(url + '&filter-test=bar'))
    assert resp.json['data']['series'] == [{'data': [1], 'label': 'Forms Count'}]


def test_statistics_forms_count_subfilters_query_integer_items(pub, formdef):
    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        if i % 2:
            formdata.data['3'] = ['1', '2']
        else:
            formdata.data['3'] = ['1']
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=1'))
    assert resp.json['data']['series'][0]['data'][0] == 10

    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=2'))
    assert resp.json['data']['series'][0]['data'][0] == 5


@pytest.mark.parametrize('anonymise', [False, True])
def test_statistics_forms_count_group_by(pub, formdef, anonymise):
    for i in range(20):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        if i % 3:
            formdata.data['1'] = True
            formdata.data['2'] = 'foo'
            formdata.data['2_display'] = 'Foo'
            formdata.data['3'] = ['bar', 'baz']
            formdata.data['3_display'] = 'Bar, Baz'
            formdata.data['4'] = {
                'data': [
                    {'1': True, '2': ['Baz'], '2_display': 'Baz'},
                    {'1': False, '2': ['Foo'], '2_display': 'Foo'},
                ]
            }
            # "Web" channel has three equivalent values
            if i == 1:
                formdata.submission_channel = 'web'
            elif i == 2:
                formdata.submission_channel = ''
            else:
                formdata.submission_channel = None
                formdata.backoffice_submission = bool(i % 2)
        elif i % 2:
            formdata.data['1'] = False
            formdata.data['2'] = 'baz'
            formdata.data['3'] = ['baz']
            formdata.data['4'] = {'data': [{'1': False, '2': ['Foo', 'Bar'], '2_display': 'Foo, Bar'}]}
            if i == 3:
                formdata.jump_status('3')
            elif i == 9:
                formdata.jump_status('3')
                formdata.jump_status('4')
            else:
                formdata.jump_status('2')
            formdata.submission_channel = 'mail'
            formdata.backoffice_submission = bool(i % 3)
        else:
            formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
        formdata.store()
        if anonymise:
            formdata.anonymise()

    # group by item field
    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'data': [13, None, None], 'label': 'Foo'},
        {'data': [3, None, None], 'label': 'baz'},
        {'data': [None, None, 4], 'label': 'None'},
    ]

    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item&time_interval=year'))
    assert resp.json['data']['x_labels'] == ['2021']
    assert resp.json['data']['series'] == [
        {'label': 'Foo', 'data': [13]},
        {'label': 'baz', 'data': [3]},
        {'label': 'None', 'data': [4]},
    ]

    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item&time_interval=hour'))
    assert resp.json['data']['x_labels'] == list(range(24))
    assert resp.json['data']['series'][0]['data'][0] == 13
    assert resp.json['data']['series'][1]['data'][0] == 3
    assert resp.json['data']['series'][2]['data'][2] == 4

    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item&time_interval=weekday'))
    assert len(resp.json['data']['x_labels']) == 7
    assert resp.json['data']['series'] == [
        {'label': 'Foo', 'data': [None, None, None, None, 13, None, None]},
        {'label': 'baz', 'data': [None, None, None, None, 3, None, None]},
        {'label': 'None', 'data': [4, None, None, None, None, None, None]},
    ]

    # hide None label
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item&hide_none_label=true'))
    assert resp.json['data']['x_labels'] == ['2021-01']
    assert resp.json['data']['series'] == [
        {'data': [13], 'label': 'Foo'},
        {'data': [3], 'label': 'baz'},
    ]

    # group by items field
    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-items'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'label': 'Bar', 'data': [13, None, None]},
        {'label': 'Baz', 'data': [16, None, None]},
        {'label': 'None', 'data': [None, None, 4]},
    ]

    # group by boolean field
    resp = get_app(pub).get(sign_uri(url + '&group-by=checkbox'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'data': [13, None, None], 'label': 'Yes'},
        {'data': [3, None, None], 'label': 'No'},
        {'data': [None, None, 4], 'label': 'None'},
    ]

    # group by boolean field inside block
    resp = get_app(pub).get(sign_uri(url + '&group-by=blockdata_bool'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'label': 'Yes', 'data': [13, None, None]},
        {'label': 'No', 'data': [16, None, None]},
        {'label': 'None', 'data': [None, None, 4]},
    ]

    # group by items field inside block
    resp = get_app(pub).get(sign_uri(url + '&group-by=blockdata_block-items'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'label': 'Foo', 'data': [16, None, None]},
        {'label': 'Bar', 'data': [3, None, None]},
        {'label': 'Baz', 'data': [13, None, None]},
        {'label': 'None', 'data': [None, None, 4]},
    ]

    # group by status
    resp = get_app(pub).get(sign_uri(url + '&group-by=status'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'label': 'New status', 'data': [13, None, 4]},
        {'label': 'End status', 'data': [1, None, None]},
        {'label': 'Middle status 1', 'data': [1, None, None]},
        {'label': 'Middle status 2', 'data': [1, None, None]},
    ]

    # group by simplified status
    resp = get_app(pub).get(sign_uri(url + '&group-by=simple-status'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'label': 'New', 'data': [13, None, 4]},
        {'label': 'Done', 'data': [1, None, None]},
        {'label': 'In progress', 'data': [2, None, None]},
    ]

    # group by channel
    resp = get_app(pub).get(sign_uri(url + '&group-by=channel'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert resp.json['data']['series'] == [
        {'data': [3, None, None], 'label': 'Mail'},
        {'data': [7, None, 4], 'label': 'Web'},
        {'data': [6, None, None], 'label': 'Backoffice'},
    ]

    # group by channel without form filter
    new_resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?group-by=channel'))
    assert new_resp.json['data']['series'] == resp.json['data']['series']

    # group by item field without time interval
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item&time_interval=none'))
    # Foo is first because it has a display value, baz is second because it has not, None is always last
    assert resp.json['data']['x_labels'] == ['Foo', 'baz', 'None']
    assert resp.json['data']['series'] == [{'data': [13, 3, 4], 'label': 'Forms Count'}]

    # group by items field without time interval
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-items&time_interval=none'))
    assert resp.json['data']['x_labels'] == ['Bar', 'Baz', 'None']
    assert resp.json['data']['series'] == [{'label': 'Forms Count', 'data': [13, 16, 4]}]

    # group by submission channel without time interval
    resp = get_app(pub).get(sign_uri(url + '&group-by=channel&time_interval=none'))
    assert resp.json['data']['x_labels'] == ['Mail', 'Web', 'Backoffice']
    assert resp.json['data']['series'] == [{'data': [3, 11, 6], 'label': 'Forms Count'}]

    # group by status without time interval
    resp = get_app(pub).get(sign_uri(url + '&group-by=status&time_interval=none'))
    assert resp.json['data']['x_labels'] == ['New status', 'End status', 'Middle status 1', 'Middle status 2']
    assert resp.json['data']['series'] == [{'data': [17, 1, 1, 1], 'label': 'Forms Count'}]

    # group by simplfified status without time interval
    resp = get_app(pub).get(sign_uri(url + '&group-by=simple-status&time_interval=none'))
    assert resp.json['data']['x_labels'] == ['New', 'Done', 'In progress']
    assert resp.json['data']['series'] == [{'label': 'Forms Count', 'data': [17, 1, 2]}]

    # check statuses order
    formdef.workflow.possible_status = list(reversed(formdef.workflow.possible_status))
    formdef.workflow.store()
    resp = get_app(pub).get(sign_uri(url + '&group-by=status&time_interval=none'))
    assert resp.json['data']['x_labels'] == ['Middle status 2', 'Middle status 1', 'End status', 'New status']
    assert resp.json['data']['series'] == [{'data': [1, 1, 1, 17], 'label': 'Forms Count'}]

    # invalid field
    resp = get_app(pub).get(sign_uri(url + '&group-by=xxx'))
    assert resp.json['data']['series'] == [{'data': [16, 0, 4], 'label': 'Forms Count'}]

    # group by on field without form filter, invalid
    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?group-by=test-item&hide_none_label=true'))
    assert resp.json['data']['series'] == [{'data': [16, 0, 4], 'label': 'Forms Count'}]


def test_statistics_forms_count_group_by_same_varname(pub, formdef):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(id='1', varname='test', label='Test', items=['foo']),
        fields.ItemField(
            id='2', varname='test', label='Test', items=['bar'], display_locations=['statistics']
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'foo'
    formdata.data['2'] = 'bar'
    formdata.store()

    url = '/api/statistics/forms/count/?form=%s' % formdef.url_name
    resp = get_app(pub).get(sign_uri(url + '&group-by=test'))
    assert resp.json['data']['series'] == [{'data': [1], 'label': 'bar'}]

    formdef.fields[0].display_locations = ['statistics']
    formdef.store()
    formdata.store()  # refresh statistics_data column

    # group by uses first field marked for statistics
    resp = get_app(pub).get(sign_uri(url + '&group-by=test'))
    assert resp.json['data']['series'] == [{'data': [1], 'label': 'foo'}]

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['2'] = 'foo'
    formdata.store()

    resp = get_app(pub).get(sign_uri(url + '&group-by=test'))
    assert resp.json['data']['series'] == [{'data': [2], 'label': 'foo'}]


def test_statistics_forms_count_group_by_form(pub):
    category_a = Category(name='Category A')
    category_a.store()

    formdef = FormDef()
    formdef.name = 'A'
    formdef.category_id = category_a.id
    formdef.store()

    for i in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2022, 1, 1, 0, 0))
        formdata.store()

    formdef = FormDef()
    formdef.name = 'B'
    formdef.category_id = category_a.id
    formdef.store()

    for i in range(5):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?include-subfilters=true'))
    assert len(resp.json['data']['subfilters']) == 2
    assert resp.json['data']['subfilters'][1] == {
        'id': 'group-by',
        'label': 'Group by',
        'options': [
            {'id': 'channel', 'label': 'Channel'},
            {'id': 'form', 'label': 'Form'},
        ],
        'has_subfilters': True,
    }

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=year'))
    assert resp.json['data']['x_labels'] == ['2021', '2022']
    assert resp.json['data']['series'] == [{'data': [5, 10], 'label': 'Forms Count'}]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=year&group-by=form'))
    assert resp.json['data']['x_labels'] == ['2021', '2022']
    assert resp.json['data']['series'] == [
        {'data': [None, 10], 'label': 'A'},
        {'data': [5, None], 'label': 'B'},
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?time_interval=none&group-by=form'))
    assert resp.json['data']['x_labels'] == ['A', 'B']
    assert resp.json['data']['series'] == [{'data': [10, 5], 'label': 'Forms Count'}]

    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?time_interval=none&group-by=form&form=category:category-a')
    )
    assert resp.json['data']['x_labels'] == ['A', 'B']
    assert resp.json['data']['series'] == [{'data': [10, 5], 'label': 'Forms Count'}]

    resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?time_interval=none&group-by=form&form=a&form=b')
    )
    assert resp.json['data']['x_labels'] == ['A', 'B']
    assert resp.json['data']['series'] == [{'data': [10, 5], 'label': 'Forms Count'}]


def test_statistics_forms_count_months_to_show(pub, formdef):
    for i in range(24):
        formdata = formdef.data_class()()
        formdata.data['2'] = 'foo' if i % 2 else 'baz'
        formdata.data['2_display'] = 'Foo' if i % 2 else 'Baz'
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2022 + i // 12, i % 12 + 1, 1, 0, 0))
        formdata.store()

    url = '/api/statistics/forms/count/'
    resp = get_app(pub).get(sign_uri(url))
    assert len(resp.json['data']['x_labels']) == 24
    assert resp.json['data']['x_labels'][0] == '2022-01'
    assert resp.json['data']['x_labels'][1] == '2022-02'
    assert resp.json['data']['x_labels'][12] == '2023-01'
    assert resp.json['data']['x_labels'][23] == '2023-12'
    assert resp.json['data']['series'][0]['data'] == [1] * 24

    resp = get_app(pub).get(sign_uri(url + '?months_to_show=12'))
    assert len(resp.json['data']['x_labels']) == 12
    assert resp.json['data']['x_labels'][0] == '2023-01'
    assert resp.json['data']['x_labels'][11] == '2023-12'
    assert resp.json['data']['series'][0]['data'] == [1] * 12

    resp = get_app(pub).get(sign_uri(url + '?months_to_show=6'))
    assert resp.json['data']['x_labels'] == ['2023-07', '2023-08', '2023-09', '2023-10', '2023-11', '2023-12']
    assert resp.json['data']['series'][0]['data'] == [1] * 6

    resp = get_app(pub).get(sign_uri(url + '?group-by=test-item&form=%s' % formdef.url_name))
    assert len(resp.json['data']['x_labels']) == 24
    assert resp.json['data']['series'][0]['label'] == 'Baz'
    assert resp.json['data']['series'][0]['data'] == [1, None] * 12
    assert resp.json['data']['series'][1]['label'] == 'Foo'
    assert resp.json['data']['series'][1]['data'] == [None, 1] * 12

    resp = get_app(pub).get(sign_uri(url + '?months_to_show=6&group-by=test-item&form=%s' % formdef.url_name))
    assert resp.json['data']['x_labels'] == ['2023-07', '2023-08', '2023-09', '2023-10', '2023-11', '2023-12']
    assert resp.json['data']['series'][0]['data'] == [1, None, 1, None, 1, None]
    assert resp.json['data']['series'][1]['data'] == [None, 1, None, 1, None, 1]


def test_statistics_cards_count(pub):
    carddef = CardDef()
    carddef.name = 'test 1'
    carddef.fields = []
    carddef.store()
    carddef.data_class().wipe()

    for _i in range(20):
        carddata = carddef.data_class()()
        carddata.just_created()
        carddata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        carddata.store()

    # apply (required) card filter
    resp = get_app(pub).get(sign_uri('/api/statistics/cards/count/?form=%s' % carddef.url_name))
    assert resp.json['data']['series'] == [{'data': [20], 'label': 'Cards Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01']

    resp = get_app(pub).get(sign_uri('/api/statistics/cards/count/?card=%s' % 'invalid'), status=404)


def test_statistics_resolution_time(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    middle_status = workflow.add_status(name='Middle status')
    end_status1 = workflow.add_status(name='End status')
    end_status2 = workflow.add_status(name='End status 2')

    # add jump from new to end
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '3'

    # add jump form new to middle and from middle to end 2
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    jump = middle_status.add_action('jump', id='_jump')
    jump.status = '4'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.store()

    freezer.move_to(datetime.date(2021, 1, 1))
    formdata_list = []
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata_list.append(formdata)

    # one formdata resolved in one day
    freezer.move_to(datetime.date(2021, 1, 2))
    formdata_list[0].jump_status('3')
    formdata_list[0].store()

    # one formdata resolved in two days, passing by middle status
    formdata_list[1].jump_status('2')
    freezer.move_to(datetime.date(2021, 1, 3))
    formdata_list[1].jump_status('4')
    formdata_list[1].store()

    # one formdata blocked in middle status for three days
    freezer.move_to(datetime.date(2021, 1, 4))
    formdata_list[2].jump_status('2')
    formdata_list[2].store()

    # by default, count forms between initial status and final statuses
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&include-subfilters=true'))
    assert resp.json['data'] == {
        'series': [
            {
                'data': [86400, 172800, 129600, 129600],
                'label': 'Time between "New status" and any final status',
            }
        ],
        'subfilters': [
            {
                'id': 'start_status',
                'label': 'Start status',
                'options': [
                    {'id': '1', 'label': 'New status'},
                    {'id': '2', 'label': 'Middle status'},
                    {'id': '3', 'label': 'End status'},
                    {'id': '4', 'label': 'End status 2'},
                ],
                'required': True,
                'default': '1',
            },
            {
                'default': 'done',
                'id': 'end_status',
                'label': 'End status',
                'options': [
                    {'id': 'done', 'label': 'Any final status'},
                    {'id': '2', 'label': 'Middle status'},
                    {'id': '3', 'label': 'End status'},
                    {'id': '4', 'label': 'End status 2'},
                ],
                'required': True,
            },
        ],
        'x_labels': ['Minimum time', 'Maximum time', 'Mean', 'Median'],
    }

    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '2 day(s) and 0 hour(s)',
        '1 day(s) and 12 hour(s)',
        '1 day(s) and 12 hour(s)',
    ]

    # same result without form filter
    new_resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/'))
    assert get_humanized_duration_serie(new_resp.json) == get_humanized_duration_serie(new_resp.json)

    # specify end status
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&end_status=3'))
    assert resp.json['data']['series'][0]['label'] == 'Time between "New status" and "End status"'
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
    ]

    # specify start status
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&start_status=2'))
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
    ]

    # specify start and end statuses
    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start_status=2&end_status=4')
    )
    assert resp.json['data']['series'][0]['label'] == 'Time between "Middle status" and "End status 2"'
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
    ]

    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start_status=1&end_status=2')
    )
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '3 day(s) and 0 hour(s)',
        '2 day(s) and 0 hour(s)',
        '2 day(s) and 0 hour(s)',
    ]

    # unknown statuses
    default_resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test'))
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&start_status=42'))
    assert resp.json == default_resp.json

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&end_status=42'))
    assert resp.json == default_resp.json

    # specify start and end statuses which does not match any formdata
    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start_status=2&end_status=3')
    )
    assert resp.json['data']['series'][0]['data'] == []

    # specify start status that is after end status
    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start_status=4&end_status=2')
    )
    assert resp.json['data']['series'][0]['label'] == 'Time between "End status 2" and "Middle status"'
    assert get_humanized_duration_serie(resp.json) == []

    # unknown form
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=xxx'), status=404)

    # form without any final status
    end_status1.add_action('choice')
    end_status2.add_action('choice')
    workflow.store()
    get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test'), status=400)


def test_statistics_resolution_time_status_loop(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    middle_status = workflow.add_status(name='Middle status')
    workflow.add_status(name='End status')

    # add jump from new to middle, middle to new, and middle to end
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    jump = middle_status.add_action('jump', id='_jump')
    jump.status = '1'
    jump = middle_status.add_action('jump', id='_jump')
    jump.status = '3'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.store()

    freezer.move_to(datetime.date(2021, 1, 1))
    formdata = formdef.data_class()()
    formdata.just_created()

    # one day after creation, jump to middle status
    freezer.move_to(datetime.date(2021, 1, 2))
    formdata.jump_status('2')
    formdata.store()

    # two days after, jump to start status
    freezer.move_to(datetime.date(2021, 1, 4))
    formdata.jump_status('1')
    formdata.store()

    # three days after, jump to middle status again
    freezer.move_to(datetime.date(2021, 1, 6))
    formdata.jump_status('2')
    formdata.store()

    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start_status=wf-new&end_status=2')
    )
    assert resp.json['data']['series'][0]['label'] == 'Time between "New status" and "Middle status"'
    # only first transition from new to middle is computed, later one is ignored
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
        '1 day(s) and 0 hour(s)',
    ]


def test_statistics_resolution_time_median(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.store()

    for i in range(2, 11):
        formdata = formdef.data_class()()
        freezer.move_to(datetime.date(2021, 1, 1))
        formdata.just_created()

        if i != 10:
            # add lots of formdata resolved in a few days
            freezer.move_to(datetime.date(2021, 1, i))
        else:
            # one formdata took 3 months
            freezer.move_to(datetime.date(2021, 4, 1))

        formdata.jump_status('2')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test'))
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',  # min
        '90 day(s) and 0 hour(s)',  # max
        '14 day(s) and 0 hour(s)',  # mean
        '5 day(s) and 0 hour(s)',  # median
    ]


def test_statistics_resolution_time_start_end_filter(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.store()

    # create formdata, the latest being the longest to resolve
    for i in range(1, 10):
        formdata = formdef.data_class()()
        freezer.move_to(datetime.date(2021, 1, i))
        formdata.just_created()
        freezer.move_to(datetime.date(2021, 1, i * 2))
        formdata.jump_status('2')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test'))
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',  # min
        '9 day(s) and 0 hour(s)',  # max
        '5 day(s) and 0 hour(s)',  # mean
        '5 day(s) and 0 hour(s)',  # median
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&start=2021-01-05'))
    assert get_humanized_duration_serie(resp.json) == [
        '5 day(s) and 0 hour(s)',  # min
        '9 day(s) and 0 hour(s)',  # max
        '7 day(s) and 0 hour(s)',  # mean
        '7 day(s) and 0 hour(s)',  # median
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&end=2021-01-05'))
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',  # min
        '4 day(s) and 0 hour(s)',  # max
        '2 day(s) and 12 hour(s)',  # mean
        '2 day(s) and 12 hour(s)',  # median
    ]

    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&start=2021-01-04&end=2021-01-05')
    )
    assert get_humanized_duration_serie(resp.json) == [
        '4 day(s) and 0 hour(s)',  # min
        '4 day(s) and 0 hour(s)',  # max
        '4 day(s) and 0 hour(s)',  # mean
        '4 day(s) and 0 hour(s)',  # median
    ]


def test_statistics_resolution_time_subfilters(pub, formdef):
    for i in range(2):
        formdata = formdef.data_class()()
        formdata.data['2'] = 'foo' if i % 2 else 'baz'
        formdata.data['2_display'] = 'Foo' if i % 2 else 'Baz'
        formdata.data['3'] = ['foo'] if i % 2 else ['bar', 'baz']
        formdata.data['3_display'] = 'Foo' if i % 2 else 'Bar, Baz'
        formdata.data['4'] = {'data': [{'2': ['foo', 'bar'], '2_display': 'Foo, Bar'}]}
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test&include-subfilters=true'))

    assert [x for x in resp.json['data']['subfilters'] if x['id'] not in ('start_status', 'end_status')] == [
        {
            'id': 'group-by',
            'label': 'Group by',
            'options': [
                {'id': 'test-item', 'label': 'Test item'},
                {'id': 'test-items', 'label': 'Test items'},
                {'id': 'blockdata_bool', 'label': 'Bool'},
                {'id': 'blockdata_block-items', 'label': 'Block items'},
                {'id': 'checkbox', 'label': 'Checkbox'},
            ],
        },
        {
            'id': 'filter-test-item',
            'label': 'Test item',
            'options': [{'id': 'baz', 'label': 'Baz'}, {'id': 'foo', 'label': 'Foo'}],
            'required': False,
        },
        {
            'id': 'filter-test-items',
            'label': 'Test items',
            'options': [
                {'id': 'bar', 'label': 'Bar'},
                {'id': 'baz', 'label': 'Baz'},
                {'id': 'foo', 'label': 'Foo'},
            ],
            'required': False,
        },
        {
            'id': 'filter-blockdata_bool',
            'label': 'Bool',
            'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
            'required': False,
        },
        {
            'id': 'filter-blockdata_block-items',
            'label': 'Block items',
            'options': [
                {'id': 'Foo', 'label': 'Foo'},
                {'id': 'Bar', 'label': 'Bar'},
                {'id': 'Baz', 'label': 'Baz'},
            ],
            'required': False,
        },
        {
            'id': 'filter-checkbox',
            'label': 'Checkbox',
            'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
            'required': False,
        },
    ]

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?form=test'))
    assert resp.json['data']['subfilters'] == []

    # all forms
    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time/?include-subfilters=true'))
    assert [x for x in resp.json['data']['subfilters']] == [
        {
            'default': 'creation',
            'id': 'start_status',
            'label': 'Start status',
            'options': [{'id': 'creation', 'label': 'Any initial status'}],
            'required': True,
        },
        {
            'default': 'done',
            'id': 'end_status',
            'label': 'End status',
            'options': [{'id': 'done', 'label': 'Any final status'}],
            'required': True,
        },
    ]

    # multiple forms
    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.category_id = formdef.category_id
    formdef2.workflow = formdef.workflow
    formdef2.fields = [x for x in formdef.fields if x.varname not in ('blockdata', 'test-items')]
    formdef2.store()

    # only common subfilters appear
    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&form=test-2&include-subfilters=true')
    )
    assert [x for x in resp.json['data']['subfilters']] == [
        {
            'default': 'creation',
            'id': 'start_status',
            'label': 'Start status',
            'options': [{'id': 'creation', 'label': 'Any initial status'}],
            'required': True,
        },
        {
            'default': 'done',
            'id': 'end_status',
            'label': 'End status',
            'options': [{'id': 'done', 'label': 'Any final status'}],
            'required': True,
        },
        {'id': 'group-by', 'label': 'Group by', 'options': [{'id': 'checkbox', 'label': 'Checkbox'}]},
        {
            'id': 'filter-checkbox',
            'label': 'Checkbox',
            'options': [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}],
            'required': False,
        },
    ]

    # remove fields from one form, no subfilters anymore
    formdef.fields = []
    formdef.workflow_id = None
    formdef.store()

    resp = get_app(pub).get(
        sign_uri('/api/statistics/resolution-time/?form=test&form=test-2&include-subfilters=true')
    )
    assert len(resp.json['data']['subfilters']) == 2
    assert resp.json['data']['subfilters'][0]['id'] == 'start_status'
    assert resp.json['data']['subfilters'][1]['id'] == 'end_status'


def test_statistics_resolution_time_subfilters_query(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.BoolField(id='1', varname='checkbox', label='Checkbox', display_locations=['statistics']),
        fields.ItemField(
            id='2',
            varname='test-item',
            label='Test item',
            items=['foo', 'bar'],
            display_locations=['statistics'],
        ),
    ]
    formdef.store()

    # formdata with checkbox checked and 'foo' selected is resolved in one day
    formdata = formdef.data_class()()
    formdata.data['1'] = True
    formdata.data['2'] = 'foo'
    freezer.move_to(datetime.date(2021, 1, 1))
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 2))
    formdata.jump_status('2')
    formdata.store()

    # formdata with checkbox unchecked and 'bar' selected is resolved in two days
    formdata = formdef.data_class()()
    formdata.data['1'] = False
    formdata.data['2'] = 'bar'
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 4))
    formdata.jump_status('2')
    formdata.store()

    url = '/api/statistics/resolution-time/?form=test'

    resp = get_app(pub).get(sign_uri(url))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '2 day(s) and 0 hour(s)',  # max
    ]

    # filter on boolean field
    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=true'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '1 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=false'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '2 day(s) and 0 hour(s)',  # min
        '2 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '&filter-checkbox=xxx'), status=400)
    assert resp.text == 'Invalid value "xxx" for "filter-checkbox".'

    # filter on item field
    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=foo'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '1 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=bar'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '2 day(s) and 0 hour(s)',  # min
        '2 day(s) and 0 hour(s)',  # max
    ]

    # combine filters
    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=foo&filter-checkbox=true'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '1 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=foo&filter-checkbox=false'))
    assert get_humanized_duration_serie(resp.json)[:2] == []


def test_statistics_resolution_time_group_by(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.BoolField(id='1', varname='checkbox', label='Checkbox', display_locations=['statistics']),
        fields.ItemField(
            id='3',
            varname='test-item',
            label='Test item (duplicate varname)',
            items=['baz'],
            display_locations=['statistics'],
        ),
        fields.ItemField(
            id='2',
            varname='test-item',
            label='Test item',
            items=['foo', 'bar'],
            display_locations=['statistics'],
        ),
    ]
    formdef.store()

    # formdata with checkbox checked and 'foo' selected is resolved in one day
    formdata = formdef.data_class()()
    formdata.data['1'] = True
    formdata.data['2'] = 'foo'
    freezer.move_to(datetime.date(2021, 1, 1))
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 2))
    formdata.jump_status('2')
    formdata.store()

    # formdata with checkbox unchecked and 'bar' selected is resolved in two days
    formdata = formdef.data_class()()
    formdata.data['1'] = False
    formdata.data['2'] = 'bar'
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 4))
    formdata.jump_status('2')
    formdata.store()

    url = '/api/statistics/resolution-time/?form=test'

    resp = get_app(pub).get(sign_uri(url))
    assert get_humanized_duration_series(resp.json) == [
        (
            'Time between "New status" and any final status',
            [
                '1 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
                '1 day(s) and 12 hour(s)',
                '1 day(s) and 12 hour(s)',
            ],
        )
    ]

    # group by boolean field
    resp = get_app(pub).get(sign_uri(url + '&group-by=checkbox'))
    assert get_humanized_duration_series(resp.json) == [
        (
            'Yes',
            [
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
            ],
        ),
        (
            'No',
            [
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
            ],
        ),
    ]

    # group by item field
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert get_humanized_duration_series(resp.json) == [
        (
            'foo',
            [
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
            ],
        ),
        (
            'bar',
            [
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
                '2 day(s) and 0 hour(s)',
            ],
        ),
    ]

    # add one more formdata with 'bar' selected, resolved in four days
    formdata = formdef.data_class()()
    formdata.data['2'] = 'bar'
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 8))
    formdata.jump_status('2')
    formdata.store()

    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert get_humanized_duration_series(resp.json) == [
        (
            'foo',
            [
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
            ],
        ),
        (
            'bar',
            [
                '2 day(s) and 0 hour(s)',
                '4 day(s) and 0 hour(s)',
                '3 day(s) and 0 hour(s)',
                '3 day(s) and 0 hour(s)',
            ],
        ),
    ]


def test_statistics_resolution_time_group_by_sort(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.ItemField(
            id='1',
            varname='test-item',
            label='Test item',
            items=['foo', 'bar', 'baz'],
            display_locations=['statistics'],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = 'bar'
    freezer.move_to(datetime.date(2021, 1, 1))
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 2))
    formdata.jump_status('2')
    formdata.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = 'baz'
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 3))
    formdata.jump_status('2')
    formdata.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = 'foo'
    formdata.just_created()
    freezer.move_to(datetime.date(2021, 1, 4))
    formdata.jump_status('2')
    formdata.store()

    url = '/api/statistics/resolution-time/?form=test'

    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert [x['label'] for x in resp.json['data']['series']] == ['foo', 'bar', 'baz']


def test_statistics_resolution_time_cards(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.add_status(name='End status')
    jump = new_status.add_action('jump', id='_jump')
    jump.status = '2'
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_id = workflow.id
    carddef.store()

    for i in range(1, 10):
        carddata = carddef.data_class()()
        freezer.move_to(datetime.date(2021, 1, i))
        carddata.just_created()
        freezer.move_to(datetime.date(2021, 1, i * 2))
        carddata.jump_status('2')
        carddata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/resolution-time-cards/?form=test'))
    assert get_humanized_duration_serie(resp.json) == [
        '1 day(s) and 0 hour(s)',
        '9 day(s) and 0 hour(s)',
        '5 day(s) and 0 hour(s)',
        '5 day(s) and 0 hour(s)',
    ]


def test_statistics_resolution_time_multiple_formdefs(pub, freezer):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    # add jump from new to end
    jump = new_status.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    def make_done_formdata(formdef, days):
        freezer.move_to(datetime.date(2021, 1, 1))

        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.data['1'] = bool(days == 1)

        freezer.move_to(datetime.timedelta(days))

        formdata.jump_status(end_status.id)
        formdata.store()

    category = Category(name='Category A')
    category.store()

    for i in range(1, 4):
        formdef = FormDef()
        formdef.name = 'test %s' % i
        formdef.fields = [
            fields.BoolField(id='1', varname='bool', label='Bool', display_locations=['statistics']),
        ]
        formdef.workflow_id = workflow.id

        if i in (1, 2):
            formdef.category_id = category.id

        formdef.store()

        make_done_formdata(formdef, days=i)

    # all forms
    url = '/api/statistics/resolution-time/'
    resp = get_app(pub).get(sign_uri(url))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '3 day(s) and 0 hour(s)',  # max
    ]

    # first and second form
    resp = get_app(pub).get(sign_uri(url + '?form=test-1&form=test-2'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '2 day(s) and 0 hour(s)',  # max
    ]

    new_resp = get_app(pub).get(sign_uri(url + '?form=category:category-a'))
    assert new_resp.json == resp.json

    # second and third form
    resp = get_app(pub).get(sign_uri(url + '?form=test-2&form=test-3'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '2 day(s) and 0 hour(s)',  # min
        '3 day(s) and 0 hour(s)',  # max
    ]

    # filter on field
    resp = get_app(pub).get(sign_uri(url + '?form=test-1&form=test-2&filter-bool=true'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '1 day(s) and 0 hour(s)',  # min
        '1 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '?form=test-1&form=test-2&filter-bool=false'))
    assert get_humanized_duration_serie(resp.json)[:2] == [
        '2 day(s) and 0 hour(s)',  # min
        '2 day(s) and 0 hour(s)',  # max
    ]

    resp = get_app(pub).get(sign_uri(url + '?form=test-2&form=test-3&filter-bool=true'))
    assert get_humanized_duration_serie(resp.json)[:2] == []

    # group by
    resp = get_app(pub).get(sign_uri(url + '?form=test-1&form=test-2&form=test-3&group-by=bool'))
    assert get_humanized_duration_series(resp.json) == [
        (
            'Yes',
            [
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
                '1 day(s) and 0 hour(s)',
            ],
        ),
        (
            'No',
            [
                '2 day(s) and 0 hour(s)',
                '3 day(s) and 0 hour(s)',
                '2 day(s) and 12 hour(s)',
                '2 day(s) and 12 hour(s)',
            ],
        ),
    ]


def test_statistics_multiple_forms_count(pub, formdef):
    formdef1 = FormDef()
    formdef1.name = 'xxx'
    formdef1.fields = [x for x in formdef.fields if x.varname != 'blockdata']
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'yyy'
    formdef2.workflow = formdef.workflow
    formdef2.fields = formdef.fields
    formdef2.store()

    for i in range(20):
        formdata = formdef1.data_class()()
        formdata.data['2'] = 'foo'
        formdata.data['2_display'] = 'Foo'
        formdata.data['3'] = ['foo']
        formdata.data['3_display'] = 'Foo'
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.store()

    for _i in range(30):
        formdata = formdef2.data_class()()
        formdata.data['2'] = 'baz'
        formdata.data['2_display'] = 'Baz'
        formdata.data['3'] = ['foo', 'bar', 'baz']
        formdata.data['3_display'] = 'Bar, Baz'
        formdata.data['4'] = {'data': [{'1': True}]}
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
        formdata.jump_status('2')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/'))
    assert resp.json['data']['series'] == [{'data': [20, 0, 30], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']

    # filter by all forms explicitely
    url = '/api/statistics/forms/count/?form=%s&form=%s' % (formdef1.url_name, formdef2.url_name)
    resp = get_app(pub).get(sign_uri(url))
    assert resp.json['data']['series'] == [{'data': [20, 0, 30], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']

    # filter on item fields
    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=foo'))
    assert resp.json['data']['series'][0]['data'] == [20]

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=baz'))
    assert resp.json['data']['series'][0]['data'] == [30]

    resp = get_app(pub).get(sign_uri(url + '&filter-test-item=bar'))
    assert resp.json['data']['series'][0]['data'] == []

    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=foo'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]

    resp = get_app(pub).get(sign_uri(url + '&filter-test-items=bar'))
    assert resp.json['data']['series'][0]['data'] == [30]

    # group by item field
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert len(resp.json['data']['series']) == 2
    assert {'data': [20, None, None], 'label': 'Foo'} in resp.json['data']['series']
    assert {'data': [None, None, 30], 'label': 'Baz'} in resp.json['data']['series']

    # filter on status
    resp = get_app(pub).get(sign_uri(url + '&filter-status=_all'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]

    resp = get_app(pub).get(sign_uri(url + '&filter-status=just_submitted'))
    assert resp.json['data']['series'][0]['data'] == [20]

    resp = get_app(pub).get(sign_uri(url + '&filter-status=done'))
    assert resp.json['data']['series'][0]['data'] == [30]

    resp = get_app(pub).get(sign_uri(url + '&filter-status=pending'))
    assert resp.json['data']['series'][0]['data'] == [20]

    # filter on status exclusive to one formdef is ignored
    resp = get_app(pub).get(sign_uri(url + '&filter-status=2'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]

    resp = get_app(pub).get(sign_uri(url + '&filter-status=rejected'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]

    # filter on block boolean field exclusive to one formdef is ignored
    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_bool=true'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]

    formdef1.fields, formdef2.fields = formdef2.fields, formdef1.fields
    formdef1.store()
    formdef2.store()

    resp = get_app(pub).get(sign_uri(url + '&filter-blockdata_bool=true'))
    assert resp.json['data']['series'][0]['data'] == [20, 0, 30]


def test_statistics_multiple_forms_count_different_ids(pub):
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': 'foo', 'text': 'Foo'}, {'id': 'bar', 'text': 'Bar'}, {'id': 'baz', 'text': 'Baz'}]
        ),
    }

    formdef1 = FormDef()
    formdef1.name = 'xxx'
    formdef1.fields = [
        fields.ItemField(
            id='1',
            varname='test-item',
            label='Test item',
            data_source=data_source,
            display_locations=['statistics'],
        ),
    ]
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'yyy'
    formdef2.fields = [
        fields.ItemField(
            id='2',
            varname='test-item',
            label='Test item',
            data_source=data_source,
            display_locations=['statistics'],
        ),
    ]
    formdef2.store()

    formdata = formdef1.data_class()()
    formdata.data['1'] = 'foo'
    formdata.data['1_display'] = 'Foo'
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.store()

    formdata = formdef2.data_class()()
    formdata.data['2'] = 'baz'
    formdata.data['2_display'] = 'Baz'
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
    formdata.store()

    url = '/api/statistics/forms/count/?form=%s&form=%s' % (formdef1.url_name, formdef2.url_name)
    resp = get_app(pub).get(sign_uri(url))
    assert resp.json['data']['series'] == [{'data': [1, 0, 1], 'label': 'Forms Count'}]
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']

    # group by item field
    resp = get_app(pub).get(sign_uri(url + '&group-by=test-item'))
    assert resp.json['data']['x_labels'] == ['2021-01', '2021-02', '2021-03']
    assert len(resp.json['data']['series']) == 2
    assert {'data': [1, None, None], 'label': 'Foo'} in resp.json['data']['series']
    assert {'data': [None, None, 1], 'label': 'Baz'} in resp.json['data']['series']


def test_statistics_multiple_forms_count_subfilters(pub, formdef):
    category_a = Category(name='Category A')
    category_a.store()

    formdef.category_id = category_a.id
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.category_id = category_a.id
    formdef2.workflow = formdef.workflow
    formdef2.fields = [x for x in formdef.fields if x.varname not in ('blockdata', 'test-items')]
    formdef2.store()

    for i in range(20):
        formdata = formdef.data_class()()
        formdata.data['2'] = 'foo'
        formdata.data['2_display'] = 'Foo'
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
        formdata.jump_status('2')
        formdata.store()

    for _i in range(30):
        formdata = formdef2.data_class()()
        formdata.data['2'] = 'baz'
        formdata.data['2_display'] = 'Baz'
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime(2021, 3, 1, 2, 0))
        formdata.store()

    resp = get_app(pub).get(
        sign_uri(
            '/api/statistics/forms/count/?form=%s&form=%s&include-subfilters=true'
            % (formdef.url_name, formdef2.url_name)
        )
    )

    # group-by subfilter shows all common fields
    group_by_filter = [x for x in resp.json['data']['subfilters'] if x['id'] == 'group-by'][0]
    assert group_by_filter['options'] == [
        {'id': 'channel', 'label': 'Channel'},
        {'id': 'form', 'label': 'Form'},
        {'id': 'simple-status', 'label': 'Simplified status'},
        {'id': 'test-item', 'label': 'Test item'},
        {'id': 'checkbox', 'label': 'Checkbox'},
        {'id': 'status', 'label': 'Status'},
    ]

    # item field subfilter shows all possible values
    item_filter = [x for x in resp.json['data']['subfilters'] if x['id'] == 'filter-test-item'][0]
    assert item_filter['options'] == [{'id': 'baz', 'label': 'Baz'}, {'id': 'foo', 'label': 'Foo'}]

    # boolean field subfilter options are not altered
    boolean_filter = [x for x in resp.json['data']['subfilters'] if x['id'] == 'filter-checkbox'][0]
    assert boolean_filter['options'] == [{'id': 'true', 'label': 'Yes'}, {'id': 'false', 'label': 'No'}]

    # block boolean and items subfilters are not shown as they are exclusive to one formdef
    assert not any(
        x
        for x in resp.json['data']['subfilters']
        if x['id'] in ('filter-blockdata_bool', 'filter-test-items')
    )

    category_resp = get_app(pub).get(
        sign_uri('/api/statistics/forms/count/?form=category:category-a&include-subfilters=true')
    )
    assert category_resp.json == resp.json

    # cannot group by form if single form is selected
    form_resp = get_app(pub).get(sign_uri('/api/statistics/forms/count/?form=test&include-subfilters=true'))
    form_group_by_filter = [x for x in form_resp.json['data']['subfilters'] if x['id'] == 'group-by'][0]
    assert [x for x in group_by_filter['options'] if x not in form_group_by_filter['options']] == [
        {'id': 'form', 'label': 'Form'}
    ]
