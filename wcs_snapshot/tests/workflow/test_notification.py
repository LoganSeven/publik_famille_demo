import datetime
import json

import pytest
from quixote import cleanup

from wcs import sessions
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.notification import SendNotificationWorkflowStatusItem

from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_notifications(pub, http_requests):
    pub.user_class.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    assert not SendNotificationWorkflowStatusItem.is_available()

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://portal/')
    assert SendNotificationWorkflowStatusItem.is_available()

    item = SendNotificationWorkflowStatusItem()
    assert item.to == ['_submitter']
    item.title = 'xxx'
    item.body = 'XXX'

    # no user
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 0

    # user
    http_requests.empty()
    user = pub.user_class()
    user.name_identifiers = ['xxx']
    user.store()
    formdata.user_id = user.id
    formdata.store()

    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
    assert json.loads(http_requests.get_last('body')) == {
        'body': 'XXX',
        'url': formdata.get_url(),
        'id': 'formdata:%s' % formdata.get_display_id(),
        'origin': '',
        'summary': 'xxx',
        'name_ids': ['xxx'],
    }

    # deleted user
    http_requests.empty()
    user.deleted_timestamp = datetime.datetime.now()
    user.store()
    item.perform(formdata)
    assert http_requests.count() == 0

    # roles (not exposed in current UI)
    http_requests.empty()
    user.deleted_timestamp = datetime.datetime.now()
    user.store()

    role = pub.role_class(name='blah')
    role.store()

    user1 = pub.user_class()
    user1.roles = [role.id]
    user1.name_identifiers = ['xxy1']
    user1.store()
    user2 = pub.user_class()
    user2.roles = [role.id]
    user2.name_identifiers = ['xxy2']
    user2.store()

    formdef.workflow_roles = {'_receiver': role.id}

    item.to = ['_receiver']
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
    assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1', 'xxy2'}

    # test inactive users are ignored
    user2.is_active = False
    user2.store()
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
    assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1'}

    user1.is_active = False
    user1.store()
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 0

    # check notifications are sent to interco portal if it exists
    user1.is_active = True
    user1.store()
    pub.site_options.set('variables', '_interco_portal_url', 'https://interco-portal/')
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://interco-portal/api/notification/add/'
    assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1'}


@pytest.mark.parametrize('i18n', [True, False])
def test_notifications_to_users_template(pub, http_requests, i18n):
    if i18n:
        pub.cfg['language'] = {
            'language': 'en',
            'multilinguism': True,
            'languages': ['en', 'fr'],
            'default_site_language': 'http',
        }

    pub.user_class.wipe()
    FormDef.wipe()

    user1 = pub.user_class(name='userA')
    user1.name_identifiers = ['xxy1']
    user1.email = 'user1@example.com'
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.name_identifiers = ['xxy2']
    user2.email = 'user2@example.com'
    user2.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdatas = []
    for i in range(2):
        formdatas.append(formdef.data_class()())

    formdatas[0].user_id = user1.id
    formdatas[1].user_id = user2.id

    for formdata in formdatas:
        formdata.just_created()
        formdata.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    assert not SendNotificationWorkflowStatusItem.is_available()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://portal/')
    assert SendNotificationWorkflowStatusItem.is_available()

    item = SendNotificationWorkflowStatusItem()
    item.to = []
    item.title = 'xxx'
    item.body = 'XXX'

    # no user template defined
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 0

    for users_template in [
        'xxy1, , xxy2',
        'user1@example.com,user2@example.com',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_nameid" }},{% endfor %}',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_email" }},{% endfor %}',
        '{{ forms|objects:"foo"|getlist:"form_user_nameid" }}',
        '{{ forms|objects:"foo"|getlist:"form_user_nameid"|list }}',
        '{{ forms|objects:"foo"|getlist:"form_user_email" }}',
        '{{ forms|objects:"foo"|getlist:"form_user_email"|list }}',
        '{{ forms|objects:"foo"|getlist:"form_user" }}',
        '{{ forms|objects:"foo"|getlist:"form_user"|list }}',
    ]:
        item.users_template = users_template
        http_requests.empty()
        item.perform(formdata)
        assert http_requests.count() == 1
        assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
        assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1', 'xxy2'}

    formdatas[1].user_id = user1.id
    formdatas[1].store()
    for users_template in [
        'xxy1,',
        'user1@example.com',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_nameid" }},{% endfor %}',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_email" }},{% endfor %}',
        '{{ forms|objects:"foo"|getlist:"form_user_nameid" }}',
        '{{ forms|objects:"foo"|getlist:"form_user_nameid"|list }}',
        '{{ forms|objects:"foo"|getlist:"form_user_email" }}',
        '{{ forms|objects:"foo"|getlist:"form_user_email"|list }}',
        '{{ forms|objects:"foo"|getlist:"form_user" }}',
        '{{ forms|objects:"foo"|getlist:"form_user"|list }}',
    ]:
        item.users_template = users_template
        http_requests.empty()
        item.perform(formdata)
        assert http_requests.count() == 1
        assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
        assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1'}

    # unknown user
    item.users_template = 'xxy1, foobar'
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 1
    assert set(json.loads(http_requests.get_last('body'))['name_ids']) == {'xxy1'}
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Failed to notify user (not found: "foobar")'
    assert logged_error.formdata_id == str(formdata.id)

    # result bad format
    item.users_template = '{{ forms|objects:"foo"|get:"foobarbaz" }}'  # None
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 0
    assert LoggedError.count() == 2
    logged_error = LoggedError.select()[1]
    assert logged_error.summary == 'Failed to notify users, bad template result (None)'
    assert logged_error.formdata_id == str(formdata.id)

    # template error
    item.users_template = '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_nameid" }},'
    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 0
    assert LoggedError.count() == 3
    logged_error = LoggedError.select()[2]
    assert logged_error.summary == 'Failed to compute template'
    assert logged_error.formdata_id == str(formdata.id)


def test_notifications_target_url(pub, http_requests):
    pub.substitutions.feed(pub)
    pub.user_class.wipe()
    user = pub.user_class()
    user.name_identifiers = ['xxx']
    user.store()

    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://portal/')

    item = SendNotificationWorkflowStatusItem()
    item.title = 'xxx'
    item.body = 'XXX'
    item.target_url = '{{ portal_url }}plop'

    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
    assert json.loads(http_requests.get_last('body')) == {
        'body': 'XXX',
        'url': 'https://portal/plop',
        'id': 'formdata:%s' % formdata.get_display_id(),
        'origin': '',
        'summary': 'xxx',
        'name_ids': ['xxx'],
    }


def test_notifications_no_body(pub, http_requests):
    pub.substitutions.feed(pub)
    pub.user_class.wipe()
    user = pub.user_class()
    user.name_identifiers = ['xxx']
    user.store()

    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://portal/')

    item = SendNotificationWorkflowStatusItem()
    item.title = 'xxx'
    item.body = None

    http_requests.empty()
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'https://portal/api/notification/add/'
    assert json.loads(http_requests.get_last('body')) == {
        'body': None,
        'url': f'http://example.net/baz/{formdata.id}/',
        'id': f'formdata:{formdata.get_display_id()}',
        'origin': '',
        'summary': 'xxx',
        'name_ids': ['xxx'],
    }
