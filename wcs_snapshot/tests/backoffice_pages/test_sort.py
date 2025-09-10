import datetime
import os
import re

import pytest
from django.utils.timezone import make_aware

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import Workflow, WorkflowCriticalityLevel

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


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


def test_backoffice_listing_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    ids = []
    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()
        ids.append(formdata.id)
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1, 10, i))
        # ordered with odd-numbered ids then even-numbered ids
        formdata.evolution[-1].time = make_aware(datetime.datetime(2015, 2, 1, 10 + i % 2, i))
        formdata.store()

    inversed_receipt_time_order = list(reversed([str(x) for x in sorted(ids)]))
    last_update_time_order = [
        str(x) for x in sorted(ids, key=lambda x: int(x) if int(x) % 2 else int(x) + 1000)
    ]

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 2
    ids = [x.strip('/') for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == inversed_receipt_time_order

    resp = app.get('/backoffice/management/form-title/?order_by=receipt_time')
    assert resp.text.count('data-link') == 2
    ids = [x.strip('/') for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == list(reversed(inversed_receipt_time_order))

    resp = app.get('/backoffice/management/form-title/?order_by=last_update_time')
    assert resp.text.count('data-link') == 2
    ids = [x.strip('/') for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == last_update_time_order

    resp = app.get('/backoffice/management/form-title/?order_by=-last_update_time')
    assert resp.text.count('data-link') == 2
    ids = [x.strip('/') for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == list(reversed(last_update_time_order))

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
        pub.site_options.set('options', 'default-sort-order', '-last_update_time')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)

    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('data-link') == 2
    ids = [x.strip('/') for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == list(reversed(last_update_time_order))

    # try invalid values
    resp = app.get('/backoffice/management/form-title/?order_by=toto.plop', status=400)


def test_backoffice_criticality_in_formdef_listing_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow_id = wf.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 10):
        formdata = data_class()
        formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1, 10, i))
        if i < 8:
            if i % 3 == 0:
                formdata.set_criticality_level(1)
            if i % 3 == 1:
                formdata.set_criticality_level(2)
            if i % 3 == 2:
                formdata.set_criticality_level(3)
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?order_by=-criticality_level&limit=100')
    order = [8, 6, 5, 3, 2, 7, 4, 1, 10, 9]
    for i, o in enumerate(order):
        if i == 9:
            break
        assert resp.text.index('>1-%s<' % o) < resp.text.index('>1-%s<' % order[i + 1])

    resp = app.get('/backoffice/management/form-title/?order_by=criticality_level&limit=100')
    reversed_order = list(reversed(order))
    for i, o in enumerate(reversed_order):
        if i == 9:
            break
        assert resp.text.index('>1-%s<' % o) < resp.text.index('>1-%s<' % reversed_order[i + 1])


def test_backoffice_criticality_in_global_listing_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow_id = wf.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 4):
        formdata = data_class()
        formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    formdata1, formdata2, formdata3, formdata4 = [
        x for x in formdef.data_class().select() if x.status == 'wf-new'
    ][:4]

    formdata1.set_criticality_level(1)
    formdata1.store()
    formdata1_str = '>%s<' % formdata1.get_display_id()
    formdata2.set_criticality_level(2)
    formdata2.store()
    formdata2_str = '>%s<' % formdata2.get_display_id()
    formdata3.set_criticality_level(2)
    formdata3.store()
    formdata3_str = '>%s<' % formdata3.get_display_id()
    formdata4_str = '>%s<' % formdata4.get_display_id()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/listing?order_by=-criticality_level&limit=100')
    assert resp.text.index(formdata1_str) > resp.text.index(formdata2_str)
    assert resp.text.index(formdata1_str) > resp.text.index(formdata3_str)
    assert resp.text.index(formdata1_str) < resp.text.index(formdata4_str)

    resp = app.get('/backoffice/management/listing?order_by=criticality_level&limit=100')
    assert resp.text.index(formdata1_str) < resp.text.index(formdata2_str)
    assert resp.text.index(formdata1_str) < resp.text.index(formdata3_str)
    assert resp.text.index(formdata1_str) > resp.text.index(formdata4_str)


def test_backoffice_varname_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata_ids = []
    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {'1': 'Bar %s' % i}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()
        formdata_ids.append(formdata.id)

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?order_by=f1')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == formdata_ids

    resp = app.get('/backoffice/management/form-title/?order_by=-f1')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == list(reversed(formdata_ids))

    resp = app.get('/backoffice/management/form-title/?order_by=foo')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == formdata_ids

    resp = app.get('/backoffice/management/form-title/?order_by=-foo')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == list(reversed(formdata_ids))


def test_backoffice_item_columns_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='First Name', varname='first_name'),
        fields.StringField(id='2', label='Last Name', varname='last_name'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_first_name }} {{ form_var_last_name }}'}
    carddef.store()
    carddef.data_class().wipe()
    card = carddef.data_class()()
    card.data = {
        '1': 'Foo',
        '2': 'Bar',
    }
    card.store()
    card2 = carddef.data_class()()
    card2.data = {
        '1': 'Bar2',
        '2': 'Foo2',
    }
    card2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(id='456', label='card field', data_source={'type': 'carddef:foo'}, varname='item'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata1 = data_class()
    formdata1.data = {'456': str(card.id), '456_display': card.default_digest}
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    formdata2 = data_class()
    formdata2.data = {'456': str(card2.id), '456_display': card2.default_digest}
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    formdata3 = data_class()
    formdata3.data = {}
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?order_by=f456')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata2.id, formdata1.id, formdata3.id]

    resp = app.get('/backoffice/management/form-title/?order_by=-f456')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata3.id, formdata1.id, formdata2.id]

    resp = app.get('/backoffice/management/form-title/?order_by=item')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata2.id, formdata1.id, formdata3.id]

    resp = app.get('/backoffice/management/form-title/?order_by=-item')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata3.id, formdata1.id, formdata2.id]


def test_backoffice_block_columns_order(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='First Name', varname='first_name'),
        fields.StringField(id='2', label='Last Name', varname='last_name'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_first_name }} {{ form_var_last_name }}'}
    carddef.store()
    carddef.data_class().wipe()
    card = carddef.data_class()()
    card.data = {
        '1': 'Foo',
        '2': 'Bar',
    }
    card.store()
    card2 = carddef.data_class()()
    card2.data = {
        '1': 'Bar2',
        '2': 'Foo2',
    }
    card2.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.ItemField(id='456', label='card field', data_source={'type': 'carddef:foo'}, varname='item'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.BlockField(id='8', label='Block', block_slug='foobar', varname='data', max_items='3'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata1 = data_class()
    formdata1.data = {
        '8': {
            'data': [{'123': 'blah', '456': card.id, '456_display': card.default_digest}],
            'schema': {},  # not important here
        },
        '8_display': 'blah',
    }
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    formdata2 = data_class()
    formdata2.data = {
        '8': {
            'data': [{'123': 'blah', '456': card2.id, '456_display': card2.default_digest}],
            'schema': {},  # not important here
        },
        '8_display': 'blah',
    }
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    formdata3 = data_class()
    formdata3.data = {
        '8': {
            'data': [{'123': 'blah'}],
            'schema': {},  # not important here
        },
        '8_display': 'blah',
    }
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?order_by=f8-456')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata2.id, formdata1.id, formdata3.id]

    resp = app.get('/backoffice/management/form-title/?order_by=-f8-456')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata3.id, formdata1.id, formdata2.id]

    resp = app.get('/backoffice/management/form-title/?order_by=data_item')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata2.id, formdata1.id, formdata3.id]

    resp = app.get('/backoffice/management/form-title/?order_by=-data_item')
    ids = [int(x.strip('/')) for x in re.findall(r'data-link="(.*?)"', resp.text)]
    assert ids == [formdata3.id, formdata1.id, formdata2.id]
