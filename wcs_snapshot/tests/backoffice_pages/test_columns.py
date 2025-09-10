import os
import re
import time

import pytest

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

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


def test_backoffice_columns(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = [
        fields.StringField(id='1', label='1st field', display_locations=['listings']),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': 'Foo Bar'}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 7  # five columns
    resp.forms['listing-settings']['1'].checked = False
    assert 'last_update_time' in resp.forms['listing-settings'].fields
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 6  # fixe columns
    assert resp.text.count('data-link') == 1  # 1 rows
    assert resp.text.count('FOO BAR') == 0  # no field 1 column

    # change column order
    assert (
        resp.forms['listing-settings']['columns-order'].value == 'id,time,last_update_time,user-label,status'
    )
    resp.forms['listing-settings']['columns-order'].value = 'user-label,id,time,last_update_time,status'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.find('<span>User Label</span>') < resp.text.find('<span>Number</span>')


def test_backoffice_channel_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns
    resp.forms['listing-settings']['submission_channel'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 7  # five columns
    assert resp.text.count('data-link') == 1  # 1 row
    assert resp.text.count('<td>Web</td>') == 1


def test_backoffice_submission_agent_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    agent = pub.user_class(name='agent')
    agent.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'submission-agent' not in resp.forms['listing-settings'].fields

    formdef.backoffice_submission_roles = [role.id]
    formdef.store()

    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns
    resp.forms['listing-settings']['submission-agent'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 7  # five columns
    assert resp.text.count('data-link') == 1  # 1 row
    assert '>agent<' not in resp.text
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert len(resp.text.splitlines()) == 2  # 1 + header line
    assert ',"agent",' not in resp.text

    for formdata in formdef.data_class().select():
        formdata.submission_agent_id = str(agent.id)
        formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['submission-agent'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>agent<') == 1

    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert len(resp.text.splitlines()) == 2  # 1 + header line
    assert resp.text.count(',"agent"') == 1


def test_backoffice_image_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = [
        fields.FileField(id='4', label='file field', display_locations=['validation', 'summary', 'listings'])
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'4': upload}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'download?f=4&thumbnail=1' not in resp.text


def test_backoffice_file_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = [
        fields.FileField(id='4', label='file field', display_locations=['validation', 'summary', 'listings'])
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    upload = PicklableUpload('a filename that is too long "and" will be ellipsised.txt', 'text/plain')
    upload.receive([b'text'])

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'4': upload}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'title="a filename that is too long &quot;and&quot; will be ellipsised.txt"' in resp
    assert '<span>a filename that is too(…).txt</span>' in resp


@pytest.mark.parametrize('settings_mode', ['new', 'legacy'])
def test_backoffice_user_columns(pub, settings_mode):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='_first_name', label='name', varname='first_name'))
    user_formdef.fields.append(fields.StringField(id='3', label='test', varname='last_name'))
    user_formdef.store()
    if settings_mode == 'new':
        pub.cfg['users']['fullname_template'] = '{{ user_var_last_name }}'
    else:
        pub.cfg['users']['field_name'] = ['3', '4']
    pub.write_cfg()

    user1 = pub.user_class(name='userA')
    user1.form_data = {'_first_name': 'toto', '3': 'nono'}
    user1.set_attributes_from_formdata(user1.form_data)
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.form_data = {'_first_name': 'tutu', '3': 'nunu'}
    user2.set_attributes_from_formdata(user2.form_data)
    user2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.user_id = user1.id if bool(i % 2) else user2.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns

    resp.forms['listing-settings']['user-label$3'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 7
    assert '<td>nono</td' in resp


def test_backoffice_card_field_columns(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.CommentField(id='0', label='...'),
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.DateField(id='2', label='Date'),
        fields.BoolField(id='3', label='Bool'),
        fields.ItemField(id='4', label='Item', data_source=datasource),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {
        '1': 'plop',
        '2': time.strptime('2020-04-24', '%Y-%m-%d'),
        '3': True,
        '4': 'A',
        '4_display': 'aa',
    }
    card.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = [
        fields.ItemField(id='4', label='card field', data_source={'type': 'carddef:foo', 'value': ''})
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '4': str(card.id),
    }
    formdata.data['4_display'] = formdef.fields[-1].store_display_value(formdata.data, '4')
    formdata.data['4_structured'] = formdef.fields[-1].store_structured_value(formdata.data, '4')
    formdata.geolocations = {'base': {'lat': 48.83, 'lon': 2.32}}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns
    assert '4$0' not in resp.forms['listing-settings'].fields
    resp.forms['listing-settings']['4$1'].checked = True
    resp.forms['listing-settings']['4$2'].checked = True
    resp.forms['listing-settings']['4$3'].checked = True
    resp.forms['listing-settings']['4$4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 10
    assert resp.text.count('data-link') == 1  # 1 row
    assert resp.text.count('<td>plop</td>') == 1
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>Yes</td>') == 1
    assert resp.text.count('<td>aa</td>') == 1

    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[1].endswith(',"plop","2020-04-24","Yes","A","aa"')

    resp_map = resp.click('Plot on a Map')
    geojson_url = re.findall(r'data-geojson-url="(.*?)"', resp_map.text)[0]
    resp_geojson = app.get(geojson_url)
    assert {
        'varname': None,
        'label': 'card field - Test',
        'value': 'plop',
        'html_value': 'plop',
    } in resp_geojson.json['features'][0]['properties']['display_fields']


def test_backoffice_card_item_file_field_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.FileField(id='1', label='Test'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_number }}'}
    carddef.store()
    carddef.data_class().wipe()

    upload = PicklableUpload('test.txt', 'text/plain')
    upload.receive([b'text'])
    card = carddef.data_class()()
    card.data = {
        '1': upload,
    }
    card.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(id='4', label='card field', data_source={'type': 'carddef:foo', 'value': ''})
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '4': str(card.id),
    }
    formdata.data['4_display'] = formdef.fields[-1].store_display_value(formdata.data, '4')
    formdata.data['4_structured'] = formdef.fields[-1].store_structured_value(formdata.data, '4')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns
    resp.forms['listing-settings']['4$1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 7
    assert resp.text.count('data-link') == 1  # 1 row
    assert resp.text.count('<td>test.txt</td>') == 1

    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[1].endswith(',"test.txt"')

    # Not an id...
    formdata.data = {
        '4': 'foobar',
    }
    formdata.store()
    resp = resp.forms['listing-settings'].submit().follow()


def test_backoffice_card_item_id_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [fields.StringField(id='1', label='Test', varname='test')]
    carddef.digest_templates = {'default': 'card {{ form_number }}'}
    carddef.store()
    carddef.data_class().wipe()

    card = carddef.data_class()()
    card.data = {'1': 'foo'}
    card.jump_status('recorded')
    card.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(id='4', label='card field', data_source={'type': 'carddef:foo', 'value': ''})
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '4': str(card.id),
    }
    formdata.data['4_display'] = formdef.fields[-1].store_display_value(formdata.data, '4')
    formdata.data['4_structured'] = formdef.fields[-1].store_structured_value(formdata.data, '4')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('</th>') == 6  # four columns
    assert '4_raw' not in resp.forms['listing-settings'].fields

    carddef.data_class().wipe()
    carddef.id_template = '{{ form_var_test }}'
    carddef.store()

    card = carddef.data_class()()
    card.data = {'1': 'foo'}
    card.jump_status('recorded')
    card.store()

    formdef.data_class().wipe()
    formdata = data_class()
    formdata.data = {'4': card.id_display}
    formdata.data['4_display'] = formdef.fields[-1].store_display_value(formdata.data, '4')
    formdata.data['4_structured'] = formdef.fields[-1].store_structured_value(formdata.data, '4')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['4_raw'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('</th>') == 7
    assert resp.text.count('data-link') == 1  # 1 row
    assert resp.pyquery('tbody tr td:last-child').text() == card.id_display

    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[1].endswith(f',"{card.id_display}"')


def test_backoffice_block_columns(pub):
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

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.ItemField(id='456', label='card field', data_source={'type': 'carddef:foo'}),
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

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [{'123': 'blah', '456': card.id, '456_display': card.default_digest}],
            'schema': {},  # not important here
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Block / card field',
        'Status (for user)',
        'Anonymised',
    ]
    # enable columns for subfields
    resp.forms['listing-settings']['8-123'].checked = True
    resp.forms['listing-settings']['8-456'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    # thead is now on two rows
    assert resp.pyquery('thead tr').length == 2
    assert [x.text_content() for x in resp.pyquery('thead tr:first-child th')] == [
        '',
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Block',
    ]
    assert [x.text_content() for x in resp.pyquery('thead tr:last-child th')] == ['Test', 'card field']
    assert '<td>blah</td>' in resp
    assert '<td>Foo Bar</td>' in resp

    formdef.fields[0].max_items = '1'
    formdef.store()

    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Block / card field',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp.forms['listing-settings']['8-456'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('th[data-field-sort-key="f8-123"]').text() == 'Test'
    assert resp.pyquery('th[data-field-sort-key="f8-456"]').text() == 'card field'

    # enable a single block subfield
    resp.forms['listing-settings']['8-123'].checked = False
    resp.forms['listing-settings']['8-456'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    # thead is now on a single row
    assert resp.pyquery('thead tr').length == 1
    assert resp.pyquery('th[data-field-sort-key="f8-456"]').text() == 'Block / card field'


def test_backoffice_block_email_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.EmailField(id='123', required='required', label='Test'),
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

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [{'123': 'blah@example.invalid'}, {'123': 'blah2@example.invalid'}],
            'schema': {},  # not important here
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    # check email addresses are displayed as links
    assert [(x.attrib['href'], x.text) for x in resp.pyquery('td a')][1:] == [
        ('mailto:blah@example.invalid', 'blah@example.invalid'),
        ('mailto:blah2@example.invalid', 'blah2@example.invalid'),
    ]


def test_backoffice_block_bool_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.BoolField(id='123', required='required', label='Test'),
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

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [
                {'123': True},
                {'123': False},
            ],
            'schema': {'123': 'bool'},
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    assert resp.text.count('<td>Yes, No</td>') == 1


def test_backoffice_block_date_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.DateField(id='123', required='required', label='Test'),
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

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [
                {'123': time.strptime('2020-04-24', '%Y-%m-%d')},
                {'123': time.strptime('2020-04-25', '%Y-%m-%d')},
            ],
            'schema': {'123': 'date'},
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    assert resp.text.count('<td>2020-04-24, 2020-04-25</td>') == 1


def test_backoffice_block_file_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', required='required', label='Test'),
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

    upload = PicklableUpload('test.txt', 'text/plain')
    upload.receive([b'text'])
    upload2 = PicklableUpload('test2.txt', 'text/plain')
    upload2.receive([b'text2'])

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [
                {'123': upload},
                {'123': upload2},
            ],
            'schema': {'123': 'file'},
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    assert resp.pyquery('tbody tr .file-field:first-child').text() == 'test.txt'
    assert resp.pyquery('tbody tr .file-field:last-child').text() == 'test2.txt'
    assert resp.click('test.txt').follow().body == b'text'
    assert resp.click('test2.txt').follow().body == b'text2'


def test_backoffice_block_text_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.TextField(id='123', required='required', label='Test'),
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

    formdata = data_class()
    formdata.data = {
        '8': {
            'data': [
                {'123': 'lorem ipsum ' * 20},
            ],
            'schema': {'123': 'text'},
        },
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Block',
        'Block / Test',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    assert resp.text.count('<td>lorem ipsum lorem ipsum lor(…)</td>') == 1


def test_backoffice_block_column_position(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='3', label='Bar'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(id='3', label='Foo'),
        fields.BlockField(id='8', label='Block', block_slug='foobar', varname='data', max_items='3'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '3': 'foobar',
        '8': {
            'data': [
                {'123': 'foo'},
            ],
            'schema': {'123': 'string'},  # not important here
        },
        '8_display': 'blah',
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Channel',
        'Foo',
        'Block',
        'Block / Test',
        'Block / Bar',
        'Status (for user)',
        'Anonymised',
    ]
    resp.forms['listing-settings']['time'].checked = False
    resp.forms['listing-settings']['last_update_time'].checked = False
    resp.forms['listing-settings']['3'].checked = True
    resp.forms['listing-settings']['8-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<th><span>Block / Test</span></th>' in resp
    assert resp.text.count('<td>foo</td>') == 1
    assert resp.forms['listing-settings']['columns-order'].value == 'id,user-label,status,3,8-123'
    assert resp.pyquery('tbody tr td').text().strip() == '1-1 - New foobar foo'  # block value is last
    resp.forms['listing-settings']['columns-order'].value = 'id,user-label,3,8-123,status'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr td').text().strip() == '1-1 - foobar foo New'  # status is last


def test_backoffice_computed_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ComputedField(
            id='4',
            label='computed field',
            type='computed',
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'4': 'foobar'}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    # check computed field is not proposed as column
    assert 'computed field' not in resp.pyquery('#columns-filter label').text()

    # check computed field is ignored if specified in query string
    resp = app.get('/backoffice/management/form-title/?4=on')
    assert 'computed field' not in resp.pyquery('th').text()


def test_backoffice_block_two_subfield_columns(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.BlockField(id='8', label='Block1', block_slug='foobar'),
        fields.BlockField(id='9', label='Block2', block_slug='foobar'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['9-123'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['save-custom-view']['title'] = 'view'
    resp.forms['save-custom-view']['visibility'] = 'owner'
    resp = resp.forms['save-custom-view'].submit()

    resp = app.get('/backoffice/management/form-title/user-view/')
    assert resp.forms['listing-settings']['8-123'].checked is False
    assert resp.forms['listing-settings']['9-123'].checked is True


def test_backoffice_digest_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='field',
            varname='foo',
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': 'foo'}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata = data_class()
    formdata.data = {'1': 'bar'}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    # check digest is not proposed as column
    assert 'digest' not in resp.pyquery('#columns-filter label').text()

    formdef.digest_templates = {'default': 'form {{ form_var_foo }}'}
    formdef.store()
    for formdata in formdef.data_class().select():
        # recompute digests
        formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['digest'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Digest',
        'Channel',
        'field',
        'Status (for user)',
        'Anonymised',
    ]
    assert {x.text for x in resp.pyquery('.cell-status + td')} == {'form foo', 'form bar'}

    resp = app.get('/backoffice/management/form-title/?digest=on&order_by=digest')
    assert [x.text for x in resp.pyquery('tbody td.lock-cell + td')] == ['form bar', 'form foo']

    resp = app.get('/backoffice/management/form-title/?digest=on&order_by=-digest')
    assert [x.text for x in resp.pyquery('tbody td.lock-cell + td')] == ['form foo', 'form bar']


def test_backoffice_unknown_status_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field', display_locations=['listings']),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': 'Foo Bar'}
    formdata.just_created()
    formdata.status = 'xxx'
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert resp.pyquery('tbody td.cell-status').text() == 'Unknown'


def test_backoffice_user_visible_status_column(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='test user visible column')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    st2.visibility = ['_receiver']
    jump = st1.add_action('jump')
    jump.status = str(st2.id)
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='field',
            varname='foo',
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow = workflow
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': 'foo'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'all'
    resp.forms['listing-settings']['user-visible-status'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x.text_content() for x in resp.pyquery('#columns-filter label')] == [
        'Number',
        'Created',
        'Last Modified',
        'User Label',
        'Status',
        'Status (for user)',
        'Channel',
        'field',
        'Anonymised',
    ]
    assert '<td class="cell-status">st2</td><td>st1</td>' in resp.text


def test_backoffice_column_labels(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [fields.StringField(id='bo1', label='bo field')]
    workflow.add_status('st1')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.geolocations = {'base': 'Geolocation'}
    formdef.fields = [
        fields.PageField(id='1', label='page'),
        fields.StringField(id='2', label='1st field'),
        fields.PageField(id='3', label='page2'),
        fields.BlockField(id='4', label='block field', block_slug='foobar'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow = workflow
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    # check labels are suffixed in column selection
    assert '1st field (page)' in [x.text_content() for x in resp.pyquery('#columns-filter label')]
    assert 'block field (page2)' in [x.text_content() for x in resp.pyquery('#columns-filter label')]
    assert 'block field / Test (page2)' in [x.text_content() for x in resp.pyquery('#columns-filter label')]
    assert 'bo field (backoffice field)' in [x.text_content() for x in resp.pyquery('#columns-filter label')]

    # check they are not suffixed in column headers
    resp.forms['listing-settings']['2'].checked = True
    resp.forms['listing-settings']['4'].checked = True
    resp.forms['listing-settings']['4-123'].checked = True
    resp.forms['listing-settings']['bo1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x.text_content() for x in resp.pyquery('th')][-4:] == [
        '1st field',
        'block field',
        'block field / Test',
        'bo field',
    ]
