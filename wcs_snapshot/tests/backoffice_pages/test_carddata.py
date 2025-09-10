import base64
import datetime
import json
import os
import urllib.parse
import uuid

import pytest
from django.utils.timezone import localtime
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import ContentSnapshotPart, Workflow, WorkflowBackofficeFieldsFormDef

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


def test_admin_card_page(pub):
    create_superuser(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    assert 'backoffice/cards/1/' in resp
    assert 'backoffice/workflows/_carddef_default/' in resp


def test_carddata_management(pub):
    CardDef.wipe()
    user = create_user(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-data')

    # no Cards entry in menu, even for admin
    user.is_admin = True
    user.store()
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-data')

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(
            id='2',
            label='Condi',
            varname='bar',
            required='required',
            condition={'type': 'django', 'value': 'form_var_foo == "ok"'},
        ),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # Cards entry for global admin
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-data')
    assert 'Cards' in resp.text
    resp = app.get('/backoffice/data/')

    # Cards entry for section admin
    user.is_admin = False
    user.store()
    pub.cfg['admin-permissions'] = {'cards': user.roles}
    pub.write_cfg()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-data')
    resp = app.get('/backoffice/data/')

    # get back to being a normal user, no Cards entry
    pub.cfg['admin-permissions'] = {}
    pub.write_cfg()
    user.is_admin = False
    user.store()
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-data')
    resp = app.get('/backoffice/data/', status=403)

    # add specific roles
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-data')
    resp = app.get('/backoffice/data/')

    carddef.backoffice_submission_roles = None
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-data')
    resp = app.get('/backoffice/data/')

    resp = app.get('/backoffice/data/')
    resp = resp.click('foo')
    assert not resp.pyquery('.actions a[href="./add/"]').text()

    carddef.backoffice_submission_roles = user.roles
    carddef.store()

    resp = app.get('/backoffice/data/')
    resp = resp.click('foo')
    assert resp.text.count('<tr') == 1  # header
    assert resp.pyquery('.actions a[href="./add/"]').text() == 'Add'
    resp = resp.click('Add')
    resp.form['f1'] = 'blah'

    live_url = resp.html.find('form').attrs['data-live-url']
    assert '/backoffice/data/foo/add/live' in live_url
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'ok'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f2'] = 'blah'

    resp = resp.form.submit('submit')
    assert resp.location.endswith('/backoffice/data/foo/1/')
    resp = resp.follow()
    assert 'Edit Card' in resp.text
    assert 'Delete Card' in resp.text

    carddata = carddef.data_class().select()[0]
    assert carddata.data == {'1': 'ok', '2': 'blah'}
    assert carddata.user_id is None
    assert carddata.submission_agent_id == str(user.id)
    assert carddata.evolution[0].who == str(user.id)
    assert 'Original Submitter' not in resp.text

    resp = app.get('/backoffice/data/')
    resp = resp.click('foo')
    assert resp.text.count('<tr') == 2  # header + row of data

    resp = resp.click('Add')
    resp = resp.form.submit('cancel')
    assert resp.location.endswith('/backoffice/data/foo/')

    # check access to a single card
    carddef.workflow_roles = {'_editor': None}
    carddef.backoffice_submission_roles = []
    carddef.store()
    assert app.get('/backoffice/data/', status=403)
    assert app.get('/backoffice/data/foo/', status=403)
    assert app.get(f'/backoffice/data/foo/{carddata.id}/', status=403)

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()
    assert app.get(f'/backoffice/data/foo/{carddata.id}/', status=403)

    # simulate dynamic dispatch
    carddata.workflow_roles = {'_editor': user.roles}
    carddata.store()
    assert app.get(f'/backoffice/data/foo/{carddata.id}/', status=200)
    assert app.get('/backoffice/data/foo/', status=200)
    assert app.get('/backoffice/data/', status=403)

    # attach carddata to user (should not give access)
    carddata.workflow_roles = {}
    carddata.user_id = user.id
    carddata.store()
    assert app.get(f'/backoffice/data/foo/{carddata.id}/', status=403)

    # give user export rights on cards from category
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-data')
    CardDefCategory.wipe()
    category = CardDefCategory(name='Foo')
    category.store()
    carddef.category_id = category.id
    carddef.store()
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-data')
    category.export_roles = [pub.role_class.get(x) for x in user.roles]
    category.store()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-data')


def test_carddata_management_categories(pub):
    user = create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.backoffice_submission_roles = None
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    carddef2 = CardDef()
    carddef2.name = 'card title 2'
    carddef2.fields = []
    carddef2.backoffice_submission_roles = None
    carddef2.workflow_roles = {'_editor': user.roles[0]}
    carddef2.store()

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store()
    cat2 = CardDefCategory(name='Bar')
    cat2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/')
    assert '<h3>Misc</h3>' not in resp.text
    assert '<h3>Foo</h3>' not in resp.text
    assert '<h3>Bar</h3>' not in resp.text

    carddef.category = cat2
    carddef.store()
    resp = app.get('/backoffice/data/')
    assert '<h3>Misc</h3>' in resp.text
    assert '<h3>Foo</h3>' not in resp.text
    assert '<h3>Bar</h3>' in resp.text

    carddef2.category = cat
    carddef2.store()
    resp = app.get('/backoffice/data/')
    assert '<h3>Misc</h3>' not in resp.text
    assert '<h3>Foo</h3>' in resp.text
    assert '<h3>Bar</h3>' in resp.text


def test_carddata_management_user_support(pub):
    user = create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/add/')
    assert 'Associated User' not in resp.text
    assert 'user_id' not in resp.form.fields

    carddef.user_support = None
    carddef.store()
    resp = app.get('/backoffice/data/foo/add/')
    assert 'Associated User' not in resp.text
    assert 'user_id' not in resp.form.fields

    carddef.user_support = 'optional'
    carddef.store()
    resp = app.get('/backoffice/data/foo/add/')
    assert 'Associated User' in resp.text
    assert 'user_id' in resp.form.fields


def test_studio_card_item_link(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop'}
    card.just_created()
    card.store()

    carddef2 = CardDef()
    carddef2.name = 'bar'
    carddef2.fields = [
        fields.ItemField(id='1', label='Test', data_source={'type': 'carddef:foo', 'value': ''}),
    ]
    carddef2.backoffice_submission_roles = user.roles
    carddef2.workflow_roles = {'_editor': user.roles[0]}
    carddef2.store()
    carddef2.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/')
    resp = resp.click('bar')
    resp = resp.click('Add')
    resp.form['f1'] = card.id
    resp = resp.form.submit('submit')
    assert resp.location.endswith('/backoffice/data/bar/1/')
    resp = resp.follow()
    resp = resp.click('card plop')
    assert '<p class="value">plop</p>' in resp

    carddata = carddef2.data_class().get(1)
    linked_cards = list(carddata.iter_target_datas())
    assert len(linked_cards) == 1
    assert linked_cards[0][0].formdef.url_name == carddef.url_name

    # check with a custom view as data source
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.store()

    carddef2.fields = [
        fields.ItemField(
            id='1',
            label='Test',
            data_source={'type': 'carddef:foo:%s' % custom_view.slug, 'value': ''},
        ),
    ]
    carddef2.store()
    resp = app.get('/backoffice/data/bar/1/')
    resp = resp.click('card plop')

    carddata = carddef2.data_class().get(1)
    linked_cards = list(carddata.iter_target_datas())
    assert len(linked_cards) == 1
    assert linked_cards[0][0].formdef.url_name == carddef.url_name

    # link to a unknown carddef
    carddef2.fields = [
        fields.ItemField(id='1', label='Test', data_source={'type': 'carddef:unknown', 'value': ''}),
    ]
    carddef2.store()

    carddata = carddef2.data_class().get(1)
    linked_cards = list(carddata.iter_target_datas())
    assert len(linked_cards) == 1
    assert linked_cards[0][0] == 'Linked object def by id unknown'

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/')
    resp = resp.click('bar')
    resp = resp.click('Add')  # no error

    # look without access rights
    carddef.backoffice_submission_roles = None
    carddef.workflow_roles = {'_editor': None}
    carddef.store()
    resp = app.get('/backoffice/data/bar/1/')
    with pytest.raises(IndexError):
        resp.click('card plop')


def test_backoffice_card_custom_id_listing(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(id='2', label='Custom id', varname='custom_id'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'foo', '2': 'foo'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'bar', '2': 'bar'}
    card2.just_created()
    card2.store()

    card3 = carddef.data_class()()
    card3.data = {'1': 'baz', '2': 'baz'}
    card3.just_created()
    card3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    assert [(x.attrib['href'], x.text) for x in resp.pyquery('table a')] == [
        ('baz/', 'baz'),
        ('bar/', 'bar'),
        ('foo/', 'foo'),
    ]

    resp = app.get('/backoffice/data/foo/?order_by=id')
    assert [(x.attrib['href'], x.text) for x in resp.pyquery('table a')] == [
        ('bar/', 'bar'),
        ('baz/', 'baz'),
        ('foo/', 'foo'),
    ]

    resp = app.get('/backoffice/data/foo/?order_by=-id')
    assert [(x.attrib['href'], x.text) for x in resp.pyquery('table a')] == [
        ('foo/', 'foo'),
        ('baz/', 'baz'),
        ('bar/', 'bar'),
    ]


def test_backoffice_card_item_link_id_template(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(id='2', label='Custom id', varname='custom_id'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop', '2': 'test'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    assert [x.attrib['href'] for x in resp.pyquery('table a')] == ['test/']
    resp = resp.click('Add')
    resp.form['f1'] = 'blah'
    resp.form['f2'] = 'blah'
    resp = resp.form.submit('submit')
    assert resp.location.endswith('/backoffice/data/foo/blah/')
    resp = resp.follow()
    assert resp.pyquery('title').text() == 'foo - blah | wcs'
    assert resp.pyquery('.breadcrumbs a')[-1].attrib['href'] == '/backoffice/data/foo/blah/'
    resp = app.get('/backoffice/data/foo/')
    assert [x.attrib['href'] for x in resp.pyquery('table a')] == ['blah/', 'test/']


def test_backoffice_card_custom_id_edit(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(id='2', label='Custom id', varname='custom_id'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'foo', '2': 'foo'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    resp = app.get(card.get_backoffice_url())
    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    resp.form['f2'] = 'bar'
    resp = resp.form.submit('submit')
    assert resp.location.endswith('/bar/')
    resp = resp.follow()


def test_backoffice_card_custom_id_edit_related(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(id='2', label='Custom id', varname='custom_id'),
        fields.ItemField(
            id='3',
            label='Link',
            data_source={'type': 'carddef:foo', 'value': ''},
            display_mode='autocomplete',
        ),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'bar', '2': 'bar'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'baz', '2': 'baz', '3': str(card.identifier), '3_display': 'card bar'}
    card2.just_created()
    card2.store()

    app = login(get_app(pub))
    resp = app.get(card2.get_backoffice_url())
    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert (
        resp.pyquery('select#form_f3').attr('data-initial-view-related-url')
        == 'http://example.net/backoffice/data/foo/bar/'
    )
    assert (
        resp.pyquery('select#form_f3').attr('data-initial-edit-related-url')
        == 'http://example.net/backoffice/data/foo/bar/wfedit-_editable'
    )


def test_backoffice_cards_import_data_from_csv(pub):
    user = create_user(pub)

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    wf = CardDef.get_default_workflow()
    wf.id = None
    st1 = wf.possible_status[0]
    create_formdata = st1.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'create_formdata'
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='"toto"'),
    ]
    wf.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.TableField(id='0', label='Table'),
        fields.MapField(id='1', label='Map'),
        fields.StringField(id='2', label='Test'),
        fields.BoolField(id='3', label='Boolean'),
        fields.ItemField(id='4', label='List', items=['item1', 'item2']),
        fields.DateField(id='5', label='Date'),
        fields.TitleField(id='6', label='Title'),
        fields.FileField(id='7', label='File'),
        fields.EmailField(id='8', label='Email'),
        fields.TextField(id='9', label='Long'),
        fields.ItemField(id='10', label='List2', data_source=data_source),
        fields.ItemsField(id='11', label='Items', data_source=data_source, required='optional'),
        fields.ItemField(id='12', label='Jsonp', data_source={'type': 'jsonp', 'value': 'xxx'}),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.workflow = wf
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))

    resp = app.get(carddef.get_url())
    assert 'Import data from a file' not in resp.text
    resp = app.get(carddef.get_url() + 'import-file', status=403)

    carddef.backoffice_submission_roles = user.roles
    carddef.store()

    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')

    assert 'Table, File are required but cannot be filled from CSV.' in resp
    assert 'Download sample CSV file for this card' not in resp
    carddef.fields[0].required = 'optional'
    carddef.fields[7].required = 'optional'
    carddef.store()

    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    sample_resp = resp.click('Download sample CSV file for this card')
    today = datetime.date.today()
    assert sample_resp.text == (
        '"Table","Map","Test","Boolean","List","Date","File","Email","Long","List2","Items","Jsonp"\r\n'
        '"will be ignored - type Table not supported",'
        '"%s",'
        '"value",'
        '"Yes",'
        '"value",'
        '"%s",'
        '"will be ignored - type File Upload not supported",'
        '"foo@example.com",'
        '"value",'
        '"value",'
        '"id1|id2|...",'
        '"value"'
        '\r\n' % ('%(lat)s;%(lon)s' % pub.get_default_position(), today)
    )

    # missing file
    resp = resp.forms[0].submit()
    assert '>required field<' in resp

    resp.forms[0]['file'] = Upload('test.csv', b'\0', 'text/csv')
    resp = resp.forms[0].submit()
    assert 'Invalid file format.' in resp

    resp.forms[0]['file'] = Upload('test.csv', b'', 'text/csv')
    resp = resp.forms[0].submit()
    assert 'Invalid CSV file.' in resp

    resp.forms[0]['file'] = Upload('test.csv', b'Test,List,Date\ndata1,item1,invalid', 'text/csv')
    resp = resp.forms[0].submit()
    assert 'CSV file contains less columns than card fields.' in resp.text

    data = [b'Table,Map,Test,Boolean,List,Date,File,Email,Long,List2,Items,Jsonp']
    for i in range(1, 150):
        data.append(
            b'table,48.81;2.37,data%d ,%s,item%d,2020-01-%02d,filename-%d,'
            b'test@localhost,"plop\nplop",1,1|2,jsonpval'
            % (i, str(bool(i % 2)).encode('utf-8'), i, i % 31 + 1, i)
        )

    resp.forms[0]['file'] = Upload('test.csv', b'\n'.join(data), 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert 'Importing data into cards' in resp
    assert carddef.data_class().count() == 149
    card1, card2 = carddef.data_class().select(order_by='id')[:2]
    assert card1.data['1'] == {'lat': 48.81, 'lon': 2.37}
    assert card1.data['2'] == 'data1'
    assert card1.data['3'] is True
    assert card1.data['5'].tm_mday == 2
    assert card1.data['9'] == 'plop\nplop'
    assert card1.data['10'] == '1'
    assert card1.data['10_display'] == 'un'
    assert card1.data['10_structured'] == {'id': '1', 'text': 'un', 'more': 'foo'}
    assert card1.data['11'] == ['1', '2']
    assert card1.data['11_display'] == 'un, deux'
    assert card1.data['11_structured'] == [
        {'id': '1', 'text': 'un', 'more': 'foo'},
        {'id': '2', 'text': 'deux', 'more': 'bar'},
    ]
    assert card1.data['12'] == 'jsonpval'
    assert card1.data['12_display'] == 'jsonpval'
    assert card2.data['2'] == 'data2'
    assert card2.data['3'] is False
    assert card2.data['5'].tm_mday == 3
    assert card2.submission_channel == 'file-import'
    assert card2.submission_agent_id == str(user.id)
    assert target_formdef.data_class().count() == 149
    assert LoggedError.count() == 0


def test_backoffice_cards_import_data_csv_user_support(pub):
    user = create_user(pub)
    user.name_identifiers = [str(uuid.uuid4())]
    user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.ItemField(id='1', label='List', items=['item1', 'item2']),
    ]
    carddef.user_support = 'optional'
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert sample_resp.text == '"User (email or UUID)","List"\r\n"value","value"\r\n'
    data = [
        b'User,List',
        b'%s,item1' % user.email.encode('utf-8'),
        b'%s,item2' % user.nameid.encode('utf-8'),
        b',item1',
        b'foobar,item2',
        b'foobar@mail.com,item2',
    ]
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', b'\n'.join(data), 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 5
    cards = carddef.data_class().select(order_by='id')
    assert cards[0].user_id == str(user.id)
    assert cards[1].user_id == str(user.id)
    assert cards[2].user_id is None
    assert cards[3].user_id is None
    assert cards[4].user_id is None

    # if no user support, user columns is ignored in import
    carddef.user_support = None
    carddef.store()
    carddef.data_class().wipe()

    data = [
        b'User',
        user.email.encode('utf-8'),
        user.nameid.encode('utf-8'),
        b'foobar',
        b'foobar@mail.com',
    ]
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', b'\n'.join(data), 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 4
    cards = carddef.data_class().select(order_by='id')
    assert [c.user_id for c in cards] == [None, None, None, None]


def test_backoffice_cards_import_data_csv_invalid_columns(pub):
    user = create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String1'),
        fields.StringField(id='2', label='String2'),
        fields.TextField(id='3', label='Text'),
    ]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')

    csv_data = '''String1,String2,Text
1,2,3
4,5,6
7,
8,9,10,11
12,13,14

'''
    resp.forms[0]['file'] = Upload('test.csv', csv_data.encode('utf-8'), 'text/csv')
    resp = resp.forms[0].submit()
    assert 'CSV file contains lines with wrong number of columns.' in resp.text
    assert '(line numbers 4, 5, 7)' in resp.text

    csv_data += '\n' * 10
    resp.forms[0]['file'] = Upload('test.csv', csv_data.encode('utf-8'), 'text/csv')
    resp = resp.forms[0].submit()
    assert 'CSV file contains lines with wrong number of columns.' in resp.text
    assert '(line numbers 4, 5, 7, 8, 9 and more)' in resp.text


def test_backoffice_cards_import_data_csv_no_backoffice_fields(pub):
    user = create_user(pub)
    user.name_identifiers = [str(uuid.uuid4())]
    user.store()

    Workflow.wipe()
    workflow = Workflow(name='form-title')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo0', varname='foo_bovar', label='bo variable'),
    ]

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String'),
        fields.ItemField(id='2', label='List', items=['item1', 'item2']),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert sample_resp.text.splitlines()[0] == '"String","List"'
    data = b'''\
"String","List"
"test","item1"
"test","item2"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 2


def test_backoffice_cards_import_data_csv_custom_id_no_update(pub):
    user = create_user(pub)
    user.name_identifiers = [str(uuid.uuid4())]
    user.store()

    Workflow.wipe()
    workflow = Workflow(name='form-title')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo0', varname='foo_bovar', label='bo variable'),
    ]
    workflow.add_status('st0')
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String', varname='custom_id'),
        fields.ItemField(id='2', label='List', items=['item1', 'item2']),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.workflow = workflow
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop', '2': 'test', '2_display': 'test', 'bo0': 'xxx'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    data = b'''\
"String","List"
"plop","item1"
"test","item2"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp.form['update_mode'] = 'skip'
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 2

    card.refresh_from_storage()
    assert card.data == {'1': 'plop', '2': 'test', '2_display': 'test', 'bo0': 'xxx'}  # no change

    other_card = carddef.data_class().select(order_by='-receipt_time')[0]
    assert other_card.data == {'1': 'test', '2': 'item2', '2_display': 'item2', 'bo0': None}
    assert other_card.id_display == 'test'


def test_backoffice_cards_import_data_csv_custom_id_update(pub):
    user = create_user(pub)
    user.name_identifiers = [str(uuid.uuid4())]
    user.store()

    Workflow.wipe()
    workflow = Workflow(name='form-title')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo0', varname='foo_bovar', label='bo variable'),
    ]
    workflow.add_status('st0')
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String', varname='custom_id'),
        fields.ItemField(id='2', label='List', items=['item1', 'item2']),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.workflow = workflow
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop', '2': 'test', 'bo0': 'xxx'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    data = b'''\
"String","List"
"plop","item1"
"test","item2"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 2

    card.refresh_from_storage()
    assert card.data == {'1': 'plop', '2': 'item1', '2_display': 'item1', 'bo0': 'xxx'}

    other_card = carddef.data_class().select(order_by='-receipt_time')[0]
    assert other_card.data == {'1': 'test', '2': 'item2', '2_display': 'item2', 'bo0': None}
    assert other_card.id_display == 'test'


def test_backoffice_cards_import_data_csv_custom_id_delete(pub):
    user = create_user(pub)
    user.name_identifiers = [str(uuid.uuid4())]
    user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String', varname='custom_id'),
        fields.StringField(id='2', label='String2'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop', '2': 'test'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'plop2', '2': 'test'}
    card2.just_created()
    card2.store()

    app = login(get_app(pub))
    data = b'''\
"String","String2"
"plop2","test"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp.form['delete_mode'] = 'delete'
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 1

    card = carddef.data_class().select()[0]
    assert card.data == {'1': 'plop2', '2': 'test'}
    assert card.id_display == 'plop2'


def test_backoffice_cards_import_data_csv_blockfield(pub):
    user = create_user(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
        fields.StringField(id='2', label='Bar', varname='bar'),
    ]
    block.digest_template = '{{ block_var_foo }}'
    block.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.backoffice_submission_roles = user.roles
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='String'),
        fields.BlockField(id='2', label='Block', varname='blockdata', block_slug='foobar', max_items='2'),
    ]
    carddef.store()

    app = login(get_app(pub))
    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert sample_resp.text.splitlines()[0] == '"String","Block"'
    assert (
        sample_resp.text.splitlines()[1]
        == '"value","will be ignored - type Block of fields (foobar) not supported"'
    )

    # block is required, error
    data = b'''\
"String","Block"
"test","item1"
"test","item2"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp = resp.forms[0].submit()
    assert 'Block is required but cannot be filled from CSV.' in resp

    # block is not required
    carddef.fields[1].required = False
    carddef.store()
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 2
    carddata1, carddata2 = carddef.data_class().select(order_by='id')
    assert carddata1.data == {'1': 'test', '2': None, '2_display': None}
    assert carddata2.data == {'1': 'test', '2': None, '2_display': None}

    # required, but max_items = '1'
    carddef.fields[1].required = True
    carddef.fields[1].max_items = '1'
    carddef.store()
    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert sample_resp.text.splitlines()[0] == '"String","Block - Foo","Block - Bar"'
    assert sample_resp.text.splitlines()[1] == '"value","value","value"'
    data = b'''\
"String","Block - Foo","Block - Bar"
"test","foo1","bar1"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 3
    carddata1, carddata2, carddata3 = carddef.data_class().select(order_by='id')
    assert carddata1.data == {'1': 'test', '2': None, '2_display': None}
    assert carddata2.data == {'1': 'test', '2': None, '2_display': None}
    assert carddata3.data == {
        '1': 'test',
        '2': {
            'data': [{'1': 'foo1', '2': 'bar1'}],
            'digests': ['foo1'],
            'schema': {'1': 'string', '2': 'string'},
        },
        '2_display': 'foo1',
    }

    # not required, with another BlockField
    carddef.fields[1].required = False
    carddef.fields.append(
        fields.BlockField(id='3', label='Block2', varname='blockdata2', block_slug='foobar', max_items='1')
    )
    carddef.store()

    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert (
        sample_resp.text.splitlines()[0]
        == '"String","Block - Foo","Block - Bar","Block2 - Foo","Block2 - Bar"'
    )
    assert sample_resp.text.splitlines()[1] == '"value","value","value","value","value"'
    data = b'''\
"String","Block - Foo","Block - Bar","Block2 - Foo","Block2 - Bar"
"test","foo2","","foo","bar"
'''
    resp = app.get('/backoffice/data/test/import-file')
    resp.forms[0]['file'] = Upload('test.csv', data, 'text/csv')
    resp = resp.forms[0].submit().follow()
    assert carddef.data_class().count() == 4
    carddata1, carddata2, carddata3, carddata4 = carddef.data_class().select(order_by='id')
    assert carddata1.data == {'1': 'test', '2': None, '2_display': None, '3': None, '3_display': None}
    assert carddata2.data == {'1': 'test', '2': None, '2_display': None, '3': None, '3_display': None}
    assert carddata3.data == {
        '1': 'test',
        '2': {
            'data': [{'1': 'foo1', '2': 'bar1'}],
            'digests': ['foo1'],
            'schema': {'1': 'string', '2': 'string'},
        },
        '2_display': 'foo1',
        '3': None,
        '3_display': None,
    }
    assert carddata4.data == {
        '1': 'test',
        '2': {'data': [{'1': 'foo2'}], 'digests': ['foo2'], 'schema': {'1': 'string'}},
        '2_display': 'foo2',
        '3': {
            'data': [{'1': 'foo', '2': 'bar'}],
            'digests': ['foo'],
            'schema': {'1': 'string', '2': 'string'},
        },
        '3_display': 'foo',
    }

    # max_items as a template (will give out a single item)
    carddef.fields[1].required = False
    carddef.fields[1].max_items = '{{ "4" }}'
    carddef.store()

    sample_resp = app.get('/backoffice/data/test/data-sample-csv')
    assert (
        sample_resp.text.splitlines()[0]
        == '"String","Block - Foo","Block - Bar","Block2 - Foo","Block2 - Bar"'
    )
    assert sample_resp.text.splitlines()[1] == '"value","value","value","value","value"'
    data = b'''\
"String","Block - Foo","Block - Bar","Block2 - Foo","Block2 - Bar"
"test","foo2","","foo","bar"
'''


def test_backoffice_cards_import_data_from_json(pub):
    user = create_user(pub)

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    wf = CardDef.get_default_workflow()
    wf.id = None
    st1 = wf.possible_status[0]
    create_formdata = st1.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'create_formdata'
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='"toto"'),
    ]
    wf.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
        fields.ItemField(id='2', label='List', varname='bar', data_source=data_source),
    ]
    block.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
        fields.BoolField(id='2', label='Boolean', varname='bool'),
        fields.ItemField(id='3', label='List', varname='item', data_source=data_source),
        fields.FileField(id='4', label='File', varname='file'),
        fields.DateField(id='5', label='Date', varname='date'),
        fields.BlockField(id='6', label='Block', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow = wf
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
                    'bool': True,
                    'item': '1',
                    'file': {
                        'filename': 'test.png',
                        'content_type': 'image/png',
                        'content': base64.encodebytes(b'...').decode(),
                    },
                    'date': '2022-07-19',
                    'blockdata': [{'foo': 'another string', 'bar': '2'}],
                }
            },
            {
                'fields': {
                    'string': 'a string 2',
                    'bool': True,
                    'item': '1',
                    'file': {
                        'filename': 'test.png',
                        'content_type': 'image/png',
                        'content': base64.encodebytes(b'...').decode(),
                    },
                    'date': '2022-07-19',
                    'blockdata': [{'foo': 'another string', 'bar': '2'}],
                }
            },
        ]
    }
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()

    carddata_export = carddef.data_class().select(order_by='id')[0].get_json_export_dict()
    assert carddata_export['workflow']['status']['id'] == 'recorded'
    assert carddata_export['fields'] == {
        'string': 'a string',
        'bool': True,
        'item': 'un',
        'item_raw': '1',
        'item_structured': {'id': '1', 'more': 'foo', 'text': 'un'},
        'file': {
            'field_id': '4',
            'filename': 'test.png',
            'content_type': 'image/png',
            'content_is_base64': True,
            'content': base64.encodebytes(b'...').decode().strip(),
            'url': 'http://example.net/api/cards/test/1/download?hash=ab5df625bc76dbd4e163bed2dd888df828f90159bb93556525c31821b6541d46',
            'thumbnail_url': 'http://example.net/api/cards/test/1/download?hash=ab5df625bc76dbd4e163bed2dd888df828f90159bb93556525c31821b6541d46&thumbnail=1',
        },
        'date': '2022-07-19',
        'blockdata': 'foobar',
        'blockdata_raw': [
            {
                'bar': 'deux',
                'bar_raw': '2',
                'bar_structured': {'id': '2', 'more': 'bar', 'text': 'deux'},
                'foo': 'another string',
            }
        ],
    }
    assert target_formdef.data_class().count() == 2
    assert LoggedError.count() == 0


def test_backoffice_cards_import_data_with_no_varname_from_json(pub):
    user = create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test'),
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
                    '_unnamed': {
                        '1': 'a string',
                    }
                }
            }
        ]
    }
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location

    carddata = carddef.data_class().select()[0]
    assert carddata.data['1'] == 'a string'


def test_backoffice_cards_import_status_from_json(pub):
    user = create_user(pub)

    workflow = CardDef.get_default_workflow()
    workflow.id = None
    st2 = workflow.add_status('status2')
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.workflow = workflow
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    data = {
        'data': [
            {
                'fields': {
                    'string': 'a string',
                },
                'workflow': {
                    'status': {
                        'id': str(st2.id),
                    }
                },
            }
        ]
    }
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location

    carddata = carddef.data_class().select()[0]
    assert carddata.status == 'wf-%s' % st2.id


def test_backoffice_cards_import_backoffice_fields_from_json(pub):
    user = create_user(pub)

    workflow = CardDef.get_default_workflow()
    workflow.id = None
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1', varname='bo_data'),
    ]
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.workflow = workflow
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    data = {
        'data': [
            {
                'fields': {
                    'string': 'a string',
                },
                'workflow': {
                    'status': {
                        'id': 'recorded',
                    },
                    'fields': {
                        'bo_data': 'foo',
                    },
                },
            }
        ]
    }
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location

    carddata = carddef.data_class().select()[0]
    assert carddata.status == 'wf-recorded'
    assert carddata.data == {'1': 'a string', 'bo1': 'foo'}


def test_backoffice_cards_import_user_from_json(pub):
    user = create_user(pub)

    user2 = pub.user_class(name='card import')
    user2.email = 'card-import@example.org'
    user2.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.backoffice_submission_roles = user.roles
    carddef.user_support = 'optional'
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    data = {
        'data': [
            {
                'fields': {
                    'string': 'a string',
                },
                'user': {
                    'email': 'card-import@example.org',
                },
            }
        ]
    }
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(data).encode(), 'application/json')
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location

    carddata = carddef.data_class().select()[0]
    assert str(carddata.user_id) == str(user2.id)


def test_backoffice_cards_update_data_from_json(pub):
    user = create_user(pub)

    workflow = CardDef.get_default_workflow()
    workflow.id = None
    workflow.add_status('status2', 'st2')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1', varname='bo_data'),
    ]
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
    ]
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.workflow_id = workflow.id
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    resp = resp.click('Export Data')
    resp.form['format'] = 'json'
    resp = resp.form.submit('submit')
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['job'][0]
    job = AfterJob.get(job_id)
    json_export = json.loads(job.result_file.get_content())
    assert len(json_export['data']) == 1
    json_export['data'][0]['fields']['string'] = 'plop 2'
    json_export['data'][0]['workflow']['fields']['bo_data'] = 'plop 2'

    # update
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()

    assert carddef.data_class().count() == 1
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 2', 'bo1': 'plop 2'}
    assert isinstance(card.evolution[0].parts[-1], ContentSnapshotPart)
    assert card.evolution[0].parts[-1].old_data == {'1': 'plop', 'bo1': None}
    assert card.evolution[0].parts[-1].new_data == {'1': 'plop 2', 'bo1': 'plop 2'}

    # update and reset backoffice fields
    json_export['data'][0]['workflow']['fields']['bo_data'] = None
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()

    assert carddef.data_class().count() == 1
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 2', 'bo1': None}
    assert isinstance(card.evolution[0].parts[-1], ContentSnapshotPart)
    assert card.evolution[0].parts[-1].old_data == {'1': 'plop 2', 'bo1': 'plop 2'}
    assert card.evolution[0].parts[-1].new_data == {'1': 'plop 2', 'bo1': None}

    # no uuid -> create
    json_export['data'][0]['uuid'] = None
    json_export['data'][0]['fields']['string'] = 'plop 3'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 2

    # uuid -> create but keep uuid
    json_export['data'][0]['uuid'] = str(uuid.uuid4())
    json_export['data'][0]['fields']['string'] = 'plop 4'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 3
    assert carddef.data_class().get_by_uuid(json_export['data'][0]['uuid']).data == {
        '1': 'plop 4',
        'bo1': None,
    }

    # invalid uuid -> ignore
    json_export['data'][0]['uuid'] = 'hello world'
    json_export['data'][0]['fields']['string'] = 'plop 5'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 4

    # ask not to update
    json_export['data'][0]['uuid'] = str(card.uuid)
    json_export['data'][0]['fields']['string'] = 'plop 6'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'skip'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 4
    assert len({x.uuid for x in carddef.data_class().select()}) == 4  # all unique UUIDs
    assert carddef.data_class().get_by_uuid(card.uuid).data == {'1': 'plop 2', 'bo1': None}

    # update and change status
    json_export['data'][0]['uuid'] = str(card.uuid)
    del json_export['data'][0]['workflow']['real_status']
    json_export['data'][0]['workflow']['real_status'] = {'id': 'st2', 'name': 'status2'}
    json_export['data'][0]['fields']['string'] = 'plop 7'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 4
    card.refresh_from_storage()
    assert card.status == 'wf-st2'
    assert [x.status for x in card.evolution] == ['wf-recorded', 'wf-st2']
    assert [x for x in card.iter_evolution_parts(ContentSnapshotPart)][-1].user_id == user.id

    # delete mode
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['delete_mode'] = 'delete'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 1
    assert str(carddef.data_class().select()[0].uuid) == str(card.uuid)


def test_backoffice_cards_update_data_from_json_custom_id(pub):
    user = create_user(pub)

    workflow = CardDef.get_default_workflow()
    workflow.id = None
    workflow.add_status('status2', 'st2')
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='string'),
        fields.StringField(id='2', label='Custom id', varname='custom_id'),
    ]
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.workflow_id = workflow.id
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop', '2': 'test'}
    card.just_created()
    card.store()

    app = login(get_app(pub))
    resp = app.get(carddef.get_url())
    resp = resp.click('Export Data')
    resp.form['format'] = 'json'
    resp = resp.form.submit('submit')
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['job'][0]
    job = AfterJob.get(job_id)
    json_export = json.loads(job.result_file.get_content())
    assert len(json_export['data']) == 1

    # update
    del json_export['data'][0]['uuid']  # ignore uuid
    json_export['data'][0]['fields']['string'] = 'plop 2'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()

    assert carddef.data_class().count() == 1
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 2', '2': 'test'}

    # no id and no uuid, but an existing id will be computed, -> update
    json_export['data'][0]['id'] = None
    json_export['data'][0]['uuid'] = None
    json_export['data'][0]['fields']['string'] = 'plop 3'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 1
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 3', '2': 'test'}

    # different id -> create new one, and id will then be updated according to template
    json_export['data'][0]['id'] = 'hello'
    json_export['data'][0]['fields']['custom_id'] = 'he'  # doesn't match
    json_export['data'][0]['fields']['string'] = 'plop 4'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'update'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 2
    assert carddef.data_class().get_by_id('he').data == {'1': 'plop 4', '2': 'he'}

    # asked not to update, but same id, it should be skipped
    json_export['data'][0]['id'] = str(card.id)
    json_export['data'][0]['fields']['string'] = 'plop 6'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'skip'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 2
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 3', '2': 'test'}

    # asked not to update, but same id would be computed, it should be skipped
    json_export['data'][0]['id'] = None
    json_export['data'][0]['uuid'] = None
    json_export['data'][0]['fields']['string'] = 'plop 7'
    json_export['data'][0]['fields']['custom_id'] = 'test'
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['update_mode'] = 'skip'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 2
    card.refresh_from_storage()
    assert card.data == {'1': 'plop 3', '2': 'test'}

    # delete mode
    resp = app.get(carddef.get_url())
    resp = resp.click('Import data from a file')
    resp.forms[0]['file'] = Upload('test.json', json.dumps(json_export).encode(), 'application/json')
    resp.form['delete_mode'] = 'delete'
    resp = resp.forms[0].submit()
    assert '/backoffice/processing?job=' in resp.location
    resp = resp.follow()
    assert carddef.data_class().count() == 1
    assert str(carddef.data_class().select()[0].uuid) == str(card.uuid)


def test_backoffice_cards_wscall_failure_display(http_requests, pub):
    user = create_user(pub)

    Workflow.wipe()
    workflow = Workflow(name='wscall')
    workflow.roles = {
        '_viewer': 'Viewer',
        '_editor': 'Editor',
    }
    st1 = workflow.add_status('Recorded', 'recorded')

    wscall = st1.add_action('webservice_call', id='_wscall')
    wscall.varname = 'xxx'
    wscall.url = 'http://remote.example.net/xml'
    wscall.action_on_bad_data = ':stop'
    wscall.record_errors = True

    again = st1.add_action('choice', id='_again')
    again.label = 'Again'
    again.by = ['_editor']
    again.status = st1.id

    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'plop'}
    carddata.just_created()
    carddata.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/data/foo/%s/' % carddata.id)
    assert 'Again' in resp.text
    resp = resp.forms[0].submit('button_again')
    resp = resp.follow()
    assert 'Error during webservice call' in resp.text

    assert LoggedError.count() == 1
    assert LoggedError.select()[0].get_formdata().data == {'1': 'plop'}


def test_card_items_links(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop1'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'plop2'}
    card2.just_created()
    card2.store()

    formdef = FormDef()
    formdef.name = 'Example'
    formdef.fields = [
        fields.ItemsField(id='1', label='test', data_source={'type': 'carddef:test', 'value': ''}),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/example/')
    resp.form['f1$element1'].value = True
    resp.form['f1$element2'].value = True

    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element1'].value == 'yes'
    assert resp.form['f1$element2'].value == 'yes'

    resp = resp.form.submit('submit').follow()  # -> final submit

    # change a label afterwards
    card2.data = {'1': 'plop2bis'}
    card2.store()

    # check cards are links in backoffice
    formdata = formdef.data_class().select()[0]
    resp = app.get(formdata.get_backoffice_url())
    assert (
        '<div class="value"><div><a href="http://example.net/backoffice/data/test/%s/">card plop1</a></div>'
        % card.id
        in resp
    )
    assert (
        '<div><a href="http://example.net/backoffice/data/test/%s/">card plop2bis</a></div></div>' % card2.id
        in resp
    )
    assert len(resp.pyquery('#form-field-label-f1 + div a')) == 2
    assert resp.pyquery('#form-field-label-f1 + div').text() == 'card plop1\ncard plop2bis'

    # check removal
    card2.remove_self()
    resp = app.get(formdata.get_backoffice_url())
    assert len(resp.pyquery('#form-field-label-f1 + div a')) == 1
    assert resp.pyquery('#form-field-label-f1 + div').text() == 'card plop1\ncard plop2'

    card.remove_self()
    resp = app.get(formdata.get_backoffice_url())
    assert len(resp.pyquery('#form-field-label-f1 + div a')) == 0
    assert resp.pyquery('#form-field-label-f1 + div').text() == 'card plop1\ncard plop2'


def test_block_card_item_link(pub):
    user = create_user(pub)
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'plop'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'plop2'}
    card2.just_created()
    card2.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='1', label='Test', data_source={'type': 'carddef:foo', 'value': ''}),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/bar/')
    resp.form['f1$element0$f1'].value = card.id
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f1'].value = card2.id
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f1'].value == str(card.id)
    assert resp.form['f1$element0$f1_label'].value == 'card plop'
    assert resp.form['f1$element1$f1'].value == str(card2.id)
    assert resp.form['f1$element1$f1_label'].value == 'card plop2'
    resp = resp.form.submit('submit')  # -> final submit
    resp = resp.follow()
    assert '<p class="value">card plop</p>' in resp
    assert '<p class="value">card plop2</p>' in resp

    # check cards are links in backoffice
    resp = app.get('/backoffice/management' + resp.request.path)
    assert (
        resp.pyquery('p.value > a[href="http://example.net/backoffice/data/foo/%s/"]' % card.id).text()
        == 'card plop'
    )
    assert (
        resp.pyquery('p.value > a[href="http://example.net/backoffice/data/foo/%s/"]' % card2.id).text()
        == 'card plop2'
    )


def test_carddata_create_user_selection(pub):
    CardDef.wipe()
    user = create_user(pub)
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': None}
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    app.get('/backoffice/data/foo/', status=403)
    resp = app.get('/backoffice/data/foo/add/')
    assert 'Associated User' not in resp

    carddef.user_support = 'optional'
    carddef.store()
    resp = app.get('/backoffice/data/foo/add/')
    assert 'Associated User' in resp
    # check looking up users works
    assert app.get('/api/users/?q=').json['data']
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> submit
    assert resp.location.endswith('backoffice/data/')  # to cards index page
    resp.follow()
    assert str(carddef.data_class().select()[0].user_id) == str(user.id)


def test_carddata_edit_user_selection(pub):
    CardDef.wipe()
    user = create_user(pub)
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'plop'}
    carddata.just_created()
    carddata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/%s/' % carddata.id)
    assert 'Edit Card' in resp.text
    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert 'Associated User' not in resp
    assert '/user-pending-forms' not in resp.text

    carddef.user_support = 'optional'
    carddef.store()
    resp = app.get('/backoffice/data/foo/%s/' % carddata.id)
    assert 'Edit Card' in resp.text
    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert 'Associated User' in resp
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> save changes
    resp = resp.follow()
    assert 'Associated User' in resp
    assert carddef.data_class().get(carddata.id).user_id == str(user.id)
    assert '/user-pending-forms' not in resp.text


def test_carddata_add_edit_related(pub):
    user = create_user(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'child'
    block.fields = [
        fields.ItemField(
            id='1',
            label='Child',
            data_source={'type': 'carddef:child'},
            display_mode='autocomplete',
        ),
    ]
    block.store()

    CardDef.wipe()
    family = CardDef()
    family.name = 'Family'
    family.fields = [
        fields.ItemField(
            id='1',
            label='RL1',
            data_source={'type': 'carddef:adult'},
            display_mode='autocomplete',
        ),
        fields.ItemField(
            id='2',
            label='RL2',
            data_source={'type': 'carddef:adult'},
            display_mode='autocomplete',
        ),
        fields.BlockField(id='3', label='Children', block_slug='child', max_items='42'),
    ]
    family.backoffice_submission_roles = user.roles
    family.workflow_roles = {'_editor': user.roles[0]}
    family.store()
    family.data_class().wipe()

    adult = CardDef()
    adult.name = 'Adult'
    adult.fields = [
        fields.StringField(
            id='1',
            label='First name',
            varname='firstname',
        ),
        fields.StringField(
            id='2',
            label='Last name',
            varname='lastname',
        ),
        fields.ItemField(
            id='3',
            label='autocompletion test',
            display_mode='autocomplete',
            items=['Foo', 'Bar', 'Three', 'Four', 'Five', 'Six'],
        ),
    ]
    adult.backoffice_submission_roles = user.roles
    adult.workflow_roles = {'_editor': user.roles[0]}
    adult.digest_templates = {'default': '{{ form_var_firstname }} {{ form_var_lastname }}'}
    adult.store()
    adult.data_class().wipe()

    child = CardDef()
    child.name = 'Child'
    child.fields = [
        fields.StringField(
            id='1',
            label='First name',
            varname='firstname',
        ),
        fields.StringField(
            id='2',
            label='Last name',
            varname='lastname',
        ),
    ]
    child.backoffice_submission_roles = user.roles
    child.workflow_roles = {'_editor': user.roles[0]}
    child.digest_templates = {'default': '{{ form_var_firstname }} {{ form_var_lastname }}'}
    child.store()
    child.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/family/add/')
    assert 'Add another RL1' in resp
    assert 'Add another RL2' in resp
    assert 'Add another Child' in resp
    assert resp.text.count('/backoffice/data/adult/add/?_popup=1') == 2
    assert '/backoffice/data/child/add/?_popup=1' in resp
    assert resp.pyquery('select#form_f1').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f1.edit-related')
    assert resp.pyquery('select#form_f1').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f1.view-related')
    assert resp.pyquery('select#form_f2').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f2.edit-related')
    assert resp.pyquery('select#form_f2').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f2.view-related')
    assert resp.pyquery('select#form_f3__element0__f1').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f3__element0__f1.edit-related')
    assert resp.pyquery('select#form_f3__element0__f1').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f3__element0__f1.view-related')
    resp_popup = app.get('/backoffice/data/adult/add/?_popup=1')
    assert 'select2.min.js' in resp_popup.text

    # no autocompletion for RL1
    family.fields[0].display_mode = []
    family.store()
    resp = app.get('/backoffice/data/family/add/')
    assert 'Add another RL1' not in resp
    assert 'Add another RL2' in resp
    assert 'Add another Child' in resp
    assert resp.text.count('/backoffice/data/adult/add/?_popup=1') == 1
    assert '/backoffice/data/child/add/?_popup=1' in resp
    assert resp.pyquery('select#form_f1').attr('data-initial-edit-related-url') is None
    assert not resp.pyquery('#edit_form_f1.edit-related')
    assert resp.pyquery('select#form_f1').attr('data-initial-view-related-url') is None
    assert not resp.pyquery('#view_form_f1.view-related')
    assert resp.pyquery('select#form_f2').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f2.edit-related')
    assert resp.pyquery('select#form_f2').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f2.view-related')
    assert resp.pyquery('select#form_f3__element0__f1').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f3__element0__f1.edit-related')
    assert resp.pyquery('select#form_f3__element0__f1').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f3__element0__f1.view-related')

    # check creation and edition in popup
    resp = app.get('/backoffice/data/child/add/?_popup=1')
    resp.form['f1'] = 'foo'
    resp.form['f2'] = 'bar'
    resp = resp.form.submit('submit')
    childdata = child.data_class().select()[0]
    assert len(childdata.get_workflow_traces()) == 1

    AfterJob.wipe()
    resp = app.get('/backoffice/data/child/%s/wfedit-_editable?_popup=1' % childdata.id)
    assert resp.form['f1'].value == 'foo'
    assert resp.form['f2'].value == 'bar'
    resp.form['f1'] = 'foo2'
    resp.form['f2'] = 'bar2'
    resp = resp.form.submit('submit')
    assert AfterJob.count() == 1  # check a single job has been created to update relations
    childdata.refresh_from_storage()
    assert len(childdata.get_workflow_traces()) == 2

    # create some data
    adultdata1 = adult.data_class()()
    adultdata1.data = {
        '1': 'foo',
        '2': 'bar 1',
        '3': 'Foo',
    }
    adultdata1.just_created()
    adultdata1.store()

    adultdata2 = adult.data_class()()
    adultdata2.data = {
        '1': 'foo',
        '2': 'bar 2',
        '3': 'Foo',
    }
    adultdata2.just_created()
    adultdata2.store()

    familydata = family.data_class()()
    familydata.data = {
        '1': str(adultdata1.id),
        '2': str(adultdata2.id),
        '3': {
            'data': [{'1': str(childdata.id), '1_display': childdata.default_digest}],
            'schema': {},  # not important here
        },
        '3_display': 'blah',
    }
    familydata.just_created()
    familydata.store()

    # check initial values
    resp = app.get('/backoffice/data/family/%s/wfedit-_editable' % familydata.id)
    assert 'Add another RL1' not in resp
    assert 'Add another RL2' in resp
    assert 'Add another Child' in resp
    assert resp.text.count('/backoffice/data/adult/add/?_popup=1') == 1
    assert '/backoffice/data/child/add/?_popup=1' in resp
    assert resp.pyquery('select#form_f1').attr('data-initial-edit-related-url') is None
    assert not resp.pyquery('#edit_form_f1.edit-related')
    assert resp.pyquery('select#form_f1').attr('data-initial-view-related-url') is None
    assert not resp.pyquery('#view_form_f1.view-related')
    assert (
        resp.pyquery('select#form_f2').attr('data-initial-edit-related-url')
        == 'http://example.net/backoffice/data/adult/%s/wfedit-_editable' % adultdata2.id
    )
    assert resp.pyquery('#edit_form_f2.edit-related')
    assert (
        resp.pyquery('select#form_f2').attr('data-initial-view-related-url')
        == 'http://example.net/backoffice/data/adult/%s/' % adultdata2.id
    )
    assert resp.pyquery('#view_form_f2.view-related')
    assert (
        resp.pyquery('select#form_f3__element0__f1').attr('data-initial-edit-related-url')
        == 'http://example.net/backoffice/data/child/%s/wfedit-_editable' % childdata.id
    )
    assert resp.pyquery('#edit_form_f3__element0__f1.edit-related')
    assert (
        resp.pyquery('select#form_f3__element0__f1').attr('data-initial-view-related-url')
        == 'http://example.net/backoffice/data/child/%s/' % childdata.id
    )
    assert resp.pyquery('#view_form_f3__element0__f1.view-related')

    # check autocomplete result
    # no query, no edit url
    autocomplete_resp = app.get(resp.pyquery('select#form_f2').attr('data-select2-url') + '?page_limit=10')
    assert autocomplete_resp.json == {
        'data': [{'id': 1, 'text': 'foo bar 1'}, {'id': 2, 'text': 'foo bar 2'}]
    }
    # no limit, no edit url
    autocomplete_resp = app.get(resp.pyquery('select#form_f2').attr('data-select2-url') + '?q=foo')
    assert autocomplete_resp.json == {
        'data': [{'id': 1, 'text': 'foo bar 1'}, {'id': 2, 'text': 'foo bar 2'}]
    }
    # ok
    autocomplete_resp = app.get(
        resp.pyquery('select#form_f2').attr('data-select2-url') + '?q=foo&page_limit=10'
    )
    assert autocomplete_resp.json == {
        'data': [
            {
                'id': 1,
                'text': 'foo bar 1',
                'edit_related_url': 'http://example.net/backoffice/data/adult/1/wfedit-_editable',
                'view_related_url': 'http://example.net/backoffice/data/adult/1/',
            },
            {
                'id': 2,
                'text': 'foo bar 2',
                'edit_related_url': 'http://example.net/backoffice/data/adult/2/wfedit-_editable',
                'view_related_url': 'http://example.net/backoffice/data/adult/2/',
            },
        ]
    }

    # check page_limit parameter has some checks
    app.get(resp.pyquery('select#form_f2').attr('data-select2-url') + '?q=foo&page_limit=xxx', status=400)

    # check there's no "add" button if card must be associated to an user
    child.submission_user_association = 'any-required'
    child.store()
    resp = app.get('/backoffice/data/family/%s/wfedit-_editable' % familydata.id)
    assert 'Add another Child' not in resp
    assert '/backoffice/data/child/add/?_popup=1' not in resp

    # user has no creation rights on child
    child.submission_user_association = 'none'
    child.backoffice_submission_roles = None
    child.store()
    resp = app.get('/backoffice/data/family/%s/wfedit-_editable' % familydata.id)
    assert 'Add another Child' not in resp
    assert '/backoffice/data/child/add/?_popup=1' not in resp

    # user has no edit and no view rights on adult
    adult.workflow_roles = {}
    adult.store()
    resp = app.get('/backoffice/data/family/%s/wfedit-_editable' % familydata.id)
    assert resp.pyquery('select#form_f2').attr('data-initial-edit-related-url') == ''
    assert resp.pyquery('#edit_form_f2.edit-related')
    assert resp.pyquery('select#form_f2').attr('data-initial-view-related-url') == ''
    assert resp.pyquery('#view_form_f2.view-related')
    autocomplete_resp = app.get(
        resp.pyquery('select#form_f2').attr('data-select2-url') + '?q=foo&page_limit=10'
    )
    assert autocomplete_resp.json == {
        'data': [
            {'id': 1, 'text': 'foo bar 1', 'edit_related_url': '', 'view_related_url': ''},
            {'id': 2, 'text': 'foo bar 2', 'edit_related_url': '', 'view_related_url': ''},
        ]
    }


def test_backoffice_card_global_interactive_action(pub):
    user = create_user(pub)

    workflow = CardDef.get_default_workflow()
    workflow.id = None
    action = workflow.add_global_action('FOOBAR')

    display = action.add_action('displaymsg')
    display.message = 'This is a message'
    display.to = []

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='test', required='required')
    )
    form_action.hide_submit_button = False
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO {{ form_workflow_form_blah_var_test }}'
    trigger = action.triggers[0]
    trigger.roles = [user.roles[0]]

    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()

    carddef.data_class().wipe()
    carddata = carddef.data_class()()
    carddata.data = {}
    carddata.just_created()
    carddata.store()

    app = login(get_app(pub))
    resp = app.get(carddata.get_url(backoffice=True))
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()  # -> error, empty action
    resp = resp.follow()  # -> back to form
    assert 'Configuration error: no available action.' in resp.text

    form_action.by = trigger.roles
    workflow.store()

    resp = app.get(carddata.get_url(backoffice=True))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert 'This is a message' in resp.text
    resp = resp.form.submit('submit')
    assert resp.pyquery(f'#form_error_fblah_{form_action.id}_1').text() == 'required field'
    resp.form[f'fblah_{form_action.id}_1'] = 'GLOBAL INTERACTIVE ACTION'
    resp = resp.form.submit('submit')
    assert resp.location == carddata.get_url(backoffice=True)
    resp = resp.follow()

    assert 'HELLO GLOBAL INTERACTIVE ACTION' in resp.text


def test_carddata_with_file(pub):
    CardDef.wipe()
    user = create_user(pub)
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='String', required='required'),
        fields.FileField(id='2', label='File'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.click('Add')
    resp.forms[0]['f2$file'] = Upload('test.txt', b'hello world')
    resp = resp.form.submit('submit')  # if will fail as string field is required
    # test file access
    assert resp.click('test.txt').body == b'hello world'
    resp.forms[0]['f1'] = 'plop'
    resp = resp.form.submit('submit').follow()  # -> submit
    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert resp.click('test.txt').body == b'hello world'  # check tempfile is ok


def test_carddata_edit_items_display(pub):
    CardDef.wipe()
    user = create_user(pub)
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.ItemsField(
            id='1', label='Test', varname='foo', data_source=data_source, required='optional', max_choice=3
        ),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/add/')
    resp.forms[0]['f1$element1'].checked = True
    resp.forms[0]['f1$element2'].checked = True
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert resp.pyquery('.field-type-items').text() == 'Test\nun\ndeux'
    resp = resp.form.submit('button_editable')
    resp = resp.follow()

    resp.forms[0]['f1$element1'].checked = False
    resp.forms[0]['f1$element2'].checked = False
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert not resp.pyquery('.field-type-items').text()


def test_carddata_history_pane_default_mode(pub):
    CardDef.wipe()
    user = create_user(pub)

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()

    app = login(get_app(pub))
    resp = app.get(carddata.get_backoffice_url())
    assert resp.pyquery('#evolution-log.folded')

    carddef.history_pane_default_mode = 'expanded'
    carddef.store()
    resp = app.get(carddata.get_backoffice_url())
    assert resp.pyquery('#evolution-log:not(.folded)')


def test_carddata_add_and_again(pub):
    CardDef.wipe()
    user = create_user(pub)
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='String', required='required'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/foo/')
    resp = resp.click('Add')
    resp.forms[0]['f1'] = 'hello'
    resp = resp.form.submit('submit').follow()

    assert resp.pyquery('#formdata-bottom-links').text() == 'Go back to listing - Add another card'
    resp = resp.click('Add another card')
    resp.forms[0]['f1'] = 'world'
    resp = resp.form.submit('submit').follow()

    # check "Add another card" link is not displayed for old entries
    carddata1, carddata2 = carddef.data_class().select()
    carddata1.receipt_time = localtime() - datetime.timedelta(days=1)
    carddata1.store()
    resp = app.get(carddata1.get_backoffice_url())
    assert resp.pyquery('#formdata-bottom-links').text() == 'Go back to listing'

    # check it's only displayed if the user is allowed to add carddata
    carddef.backoffice_submission_roles = {}
    carddef.store()
    resp = app.get(carddata2.get_backoffice_url())
    assert resp.pyquery('#formdata-bottom-links').text() == 'Go back to listing'

    # check it's only displayed for user who created the card
    carddef.backoffice_submission_roles = user.roles
    carddef.store()
    carddata2.submission_agent_id = None
    carddata2.store()
    resp = app.get(carddata2.get_backoffice_url())
    assert resp.pyquery('#formdata-bottom-links').text() == 'Go back to listing'


def test_backoffice_carddata_wfedit_redirects(pub):
    user = create_user(pub)

    Workflow.wipe()
    workflow = Workflow(name='wfedit')
    st = workflow.add_status('Status1')
    wfedit = st.add_action('editable')
    wfedit.by = [user.roles[0]]
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.StringField(id='1', label='String', required='required'),
        fields.PageField(id='2', label='2nd page'),
    ]
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'plop'}
    carddata.just_created()
    carddata.jump_status('Status1')
    carddata.store()

    app = login(get_app(pub))

    edit_url = f'{carddata.get_backoffice_url()}wfedit-_editable'
    urls = {
        'ReturnURL': 'http://example.net/?return',
        'cancelurl': 'http://example.net/?cancel',
    }
    # tests all combinations of ReturnURL/cancelurl
    for get_params in [(), ('cancelurl',), ('ReturnURL',), ('ReturnURL', 'cancelurl')]:
        params = {k: urls[k] for k in get_params if k in urls}
        # check params length just to avoid typos
        assert len(params) == len(get_params), params

        resp = app.get(edit_url, params=params)
        form = resp.form
        # ensure we have data. no draft are created
        assert form['f1'].value == 'plop', params
        resp = form.submit('submit')
        resp = resp.form.submit('submit', status=302)
        assert resp.location == (params.get('ReturnURL') or carddata.get_backoffice_url())

        resp = app.get(edit_url, params=params)
        form = resp.form
        assert form['f1'].value == 'plop', params
        resp = form.submit('cancel', status=302)
        assert resp.location == (params.get('cancelurl') or carddata.get_backoffice_url())

        resp = app.get(edit_url, params=params)
        form = resp.form
        assert form['f1'].value == 'plop', params
        resp = form.submit('submit')
        resp = resp.form.submit('cancel', status=302)
        assert resp.location == (params.get('cancelurl') or carddata.get_backoffice_url())

    # ensure that bad urls are rejected
    for param in ['ReturnURL', 'cancelurl']:
        app.get(edit_url, params={param: 'https://fishing.bzh'}, status=400)
