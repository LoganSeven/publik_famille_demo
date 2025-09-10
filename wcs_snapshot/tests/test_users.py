import datetime
import json

import pytest
import responses
from django.core.management import call_command
from django.utils.timezone import localtime

from wcs import fields
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.sql_criterias import NotNull
from wcs.variables import LazyUser
from wcs.workflows import Evolution

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    pub.load_site_options()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_get_users_with_name_identifier(pub):
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Pierre'
    user.name_identifiers = ['foo']
    user.store()

    user = pub.user_class()
    user.name = 'Papier'
    user.store()

    assert len(pub.user_class.get_users_with_name_identifier('foo')) == 1
    assert pub.user_class.get_users_with_name_identifier('foo')[0].name == 'Pierre'


def test_user_substitution_variables(pub):
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Pierre'
    user.email = 'test@example.net'
    user.name_identifiers = ['foo']
    user.store()

    assert user.get_substitution_variables().get('session_user_display_name') == 'Pierre'
    assert user.get_substitution_variables().get('session_user_nameid') == 'foo'
    assert user.get_substitution_variables().get('session_user_email') == 'test@example.net'

    lazy_user = LazyUser(user)
    assert lazy_user.email == user.email
    assert lazy_user.nameid == user.name_identifiers[0]
    assert lazy_user.display_name == user.name


def test_get_users_with_role(pub):
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Pierre'
    user.roles = [1]
    user.store()

    user = pub.user_class()
    user.name = 'Papier'
    user.store()

    assert len(pub.user_class.get_users_with_role(1)) == 1
    assert pub.user_class.get_users_with_role(1)[0].name == 'Pierre'


def test_get_users_with_email(pub):
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Pierre'
    user.email = 'pierre@example.org'
    user.store()

    user = pub.user_class()
    user.name = 'Papier'
    user.email = 'papier@example.org'
    user.store()

    assert len(pub.user_class.get_users_with_email('pierre@example.org')) == 1
    assert pub.user_class.get_users_with_email('pierre@example.org')[0].name == 'Pierre'


def test_user_formdef_getattr(pub):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='3', label='test', varname='plop'),
        fields.StringField(id='9', label='noop', varname='get_formdef'),
    ]
    formdef.store()

    user = pub.user_class()
    assert user.plop is None
    assert user.get_formdef()  # get_formdef is not overrided by varname "get_formdef"

    user.form_data = {'3': 'Bar', '9': 'Foo'}
    assert user.plop == 'Bar'
    # noqa pylint: disable=comparison-with-callable
    assert user.get_formdef != 'Foo'

    with pytest.raises(AttributeError):
        # noqa pylint: disable=pointless-statement
        user.xxx


def test_user_fullname(pub):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='3', label='test', varname='plop'),
    ]
    formdef.store()

    user = pub.user_class()
    user.form_data = {'3': '<b>Bar'}

    # legacy, list of field ids
    pub.cfg['users']['field_name'] = ['3']
    pub.cfg['users']['fullname_template'] = ''
    pub.write_cfg()

    user.set_attributes_from_formdata(user.form_data)
    assert user.name == '<b>Bar'

    # new, template
    pub.cfg['users']['fullname_template'] = '{{ user_var_plop|default:"" }}'
    pub.write_cfg()

    user.form_data = {'3': '<b>Foo'}
    user.set_attributes_from_formdata(user.form_data)
    assert user.name == '<b>Foo'

    pub.cfg['users']['fullname_template'] = '{% if plop %}'  # error
    pub.write_cfg()
    user.set_attributes_from_formdata(user.form_data)
    assert user.name == '!template error! (None)'


def test_user_phone_number(pub):
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='3', label='test', varname='plop'),
    ]
    formdef.store()

    user = pub.user_class()
    user.form_data = {'3': '0102030405'}

    assert user.get_formatted_phone() is None

    pub.cfg['users']['field_phone'] = '3'
    pub.write_cfg()

    assert user.get_formatted_phone() == '01 02 03 04 05'

    user.form_data = {}
    assert user.get_formatted_phone() is None


def test_user_keepalive(pub):
    LoggedError.wipe()
    pub.user_class.wipe()
    FormDef.wipe()

    user = pub.user_class()
    user.name = 'Pierre'
    user.name_identifiers = ['foo']
    user.store()

    user = pub.user_class()
    user.name = 'Papier'
    user.name_identifiers = ['bar']
    user.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/users/synchronization/', json={'err': 0})
        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 0

        pub.cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
        pub.write_cfg()

        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 1
        assert 'signature' in rsps.calls[0].request.url
        assert json.loads(rsps.calls[0].request.body) == {'known_uuids': ['bar'], 'keepalive': True}

        formdata.jump_status('finished')
        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 2
        assert json.loads(rsps.calls[1].request.body) == {'known_uuids': [], 'keepalive': True}

        rsps.post('http://idp.example.net/api/users/synchronization/', status=400, json={'err': 1})
        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 3
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Failed to call keepalive API (status: 400)'

    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'card1'
    carddef.fields = []
    carddef.user_support = False
    carddef.store()

    carddata = carddef.data_class()()
    carddata.user_id = user.id
    carddata.just_created()
    carddata.store()

    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/users/synchronization/', json={'err': 0})
        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 1
        assert json.loads(rsps.calls[0].request.body) == {'known_uuids': [], 'keepalive': True}

        carddef.user_support = True
        carddef.store()

        pub.user_class.keepalive_users()
        assert len(rsps.calls) == 2
        assert json.loads(rsps.calls[1].request.body) == {'known_uuids': ['bar'], 'keepalive': True}


def test_user_sync(pub):
    LoggedError.wipe()
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='_first_name', label='first name', varname='first_name'),
        fields.StringField(id='_last_name', label='last name', varname='last_name'),
        fields.EmailField(id='_email', label='email', varname='email'),
    ]
    formdef.store()

    pub.cfg['users']['field_email'] = '_email'
    pub.cfg['users'][
        'fullname_template'
    ] = '{{user_var_first_name|default:""}} {{user_var_last_name|default:""}}'
    pub.write_cfg()

    pub.user_class.wipe()

    for i in range(10):
        user = pub.user_class()
        user.form_data = {'_email': f'bar{i}@example.net', '_first_name': 'foo', '_last_name': f'bar{i}'}
        user.set_attributes_from_formdata(user.form_data)
        user.name_identifiers = [f'bar{i}']
        user.store()

    with responses.RequestsMock() as rsps:
        pub.user_class.sync_users()
        assert len(rsps.calls) == 0

        pub.cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
        pub.write_cfg()

        rsps.post(
            'http://idp.example.net/api/users/synchronization/',
            json={
                'err': 0,
                'unknown_uuids': ['bar4', 'bar5'],
            },
        )
        rsps.get(
            'http://idp.example.net/api/users/',
            json={
                'next': None,
                'previous': None,
                'results': [
                    {
                        'full_name': 'foo baz 1',
                        'uuid': 'bar1',
                        'first_name': 'foo',
                        'last_name': 'baz 1',
                        'email': 'foobaz1@example.net',
                    }
                ],
            },
        )
        pub.user_class.sync_users()
        assert len(rsps.calls) == 1
        assert LoggedError.count() == 1
        assert (
            LoggedError.select()[0].summary
            == 'Deletion ratio is abnormally high (20.0%), aborting unknown users deletion'
        )

        # add more users
        for i in range(10, 50):
            user = pub.user_class()
            user.form_data = {'_email': f'bar{i}@example.net', '_first_name': 'foo', '_last_name': f'bar{i}'}
            user.set_attributes_from_formdata(user.form_data)
            user.name_identifiers = [f'bar{i}']
            user.store()

        pub.user_class.sync_users()
        assert len(rsps.calls) == 3
        assert len(pub.user_class.select([NotNull('deleted_timestamp')])) == 2
        user = pub.user_class.get_users_with_name_identifier('bar1')[0]
        assert user.form_data == {
            '_first_name': 'foo',
            '_last_name': 'baz 1',
            '_email': 'foobaz1@example.net',
        }
        assert user.email == 'foobaz1@example.net'
        assert user.name == 'foo baz 1'

    # calls with errors
    LoggedError.wipe()
    with responses.RequestsMock() as rsps:

        rsps.post(
            'http://idp.example.net/api/users/synchronization/',
            json={
                'err': 0,
                'unknown_uuids': ['bar4', 'bar5'],
            },
        )
        rsps.get('http://idp.example.net/api/users/', status=400)
        pub.user_class.sync_users()
        assert len(rsps.calls) == 2
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Failed to call users API (status: 400)'

    LoggedError.wipe()
    with responses.RequestsMock() as rsps:
        rsps.post(
            'http://idp.example.net/api/users/synchronization/',
            json={
                'err': 0,
                'unknown_uuids': ['bar4', 'bar5'],
            },
        )
        rsps.get('http://idp.example.net/api/users/', json={'err': 1}, status=200)
        pub.user_class.sync_users()
        assert len(rsps.calls) == 2
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == "Failed to call users API (response: {'err': 1})"

    LoggedError.wipe()
    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/users/synchronization/', json={'err': 1}, status=200)
        pub.user_class.sync_users()
        assert len(rsps.calls) == 1
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == "Failed to call keepalive API (response: {'err': 1})"

    LoggedError.wipe()
    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/users/synchronization/', status=400)
        pub.user_class.sync_users()
        assert len(rsps.calls) == 1
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Failed to call keepalive API (status: 400)'

    # call with no user
    pub.user_class.wipe()
    pub.user_class.sync_users()


def test_clean_deleted_users(pub):
    User = pub.user_class

    User.wipe()
    FormDef.wipe()
    CardDef.wipe()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()
    data_class = formdef.data_class()

    carddef = CardDef()
    carddef.name = 'barfoo'
    carddef.url_name = 'barfoo'
    carddef.fields = []
    carddef.store()
    card_data_class = carddef.data_class()

    user1 = User()
    user1.name = 'Pierre'
    user1.deleted_timestamp = datetime.datetime.now()
    user1.store()

    user2 = User()
    user2.name = 'Jean'
    user2.deleted_timestamp = datetime.datetime.now()
    user2.store()

    user3 = User()
    user3.name = 'Michel'
    user3.deleted_timestamp = datetime.datetime.now()
    user3.store()

    user4 = User()
    user4.name = 'Martin'
    user4.deleted_timestamp = datetime.datetime.now()
    user4.store()

    user5 = User()
    user5.name = 'Alain'
    user5.deleted_timestamp = datetime.datetime.now()
    user5.store()

    formdata1 = data_class()
    formdata1.user_id = user1.id
    evo = Evolution(formdata=formdata1)
    evo.time = localtime()
    evo.who = user4.id
    evo2 = Evolution(formdata=formdata1)
    evo2.time = localtime()
    evo2.who = '_submitter'
    formdata1.evolution = [evo, evo2]
    formdata1.workflow_roles = {'_received': '_user:%s' % user5.id}
    formdata1.store()

    carddata1 = card_data_class()
    carddata1.user_id = user3.id
    carddata1.store()

    assert User.count() == 5

    pub.clean_deleted_users()

    assert {user.name for user in User.select()} == {'Pierre', 'Michel', 'Martin', 'Alain'}

    data_class.wipe()
    card_data_class.wipe()

    call_command('cron', job_name='clean_deleted_users', domain='example.net')

    assert User.count() == 0


def test_normal_users_test_users_isolation(pub):
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Jean'
    user.email = 'jean@example.com'
    user.store()

    user = pub.user_class()
    user.name = 'Jean'
    user.email = 'jean@example.com'
    user.test_uuid = '42'
    user.store()

    assert len(pub.user_class.select()) == 1
    assert pub.user_class.select()[0].test_uuid is None

    assert len(pub.user_class.get_users_with_email('jean@example.com')) == 1
    assert pub.user_class.get_users_with_email('jean@example.com')[0].test_uuid is None
