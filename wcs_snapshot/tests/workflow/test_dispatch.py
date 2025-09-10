import pytest
from quixote import cleanup

from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.dispatch import DispatchWorkflowStatusItem

from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_config(req)
    return pub


def test_dispatch(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    item = DispatchWorkflowStatusItem()

    formdata = formdef.data_class()()
    item.perform(formdata)
    assert not formdata.workflow_roles

    formdata = formdef.data_class()()
    item.role_key = '_receiver'
    item.role_id = role.id
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}


def test_dispatch_multi(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    role2 = pub.role_class(name='xxx2')
    role2.store()
    role3 = pub.role_class(name='xxx3')
    role3.store()

    item = DispatchWorkflowStatusItem()

    formdata = formdef.data_class()()
    item.perform(formdata)
    assert not formdata.workflow_roles

    formdata = formdef.data_class()()
    item.role_key = '_receiver'
    item.role_id = role.id
    item.operation_mode = 'add'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}

    item.role_id = role2.id
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id, role2.id]}

    item.operation_mode = 'set'
    item.role_id = role3.id
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role3.id]}

    # test adding to function defined at the formdef level
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdata.workflow_roles = {}
    formdata.store()

    item.operation_mode = 'add'
    item.role_id = role2.id
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id, role2.id]}

    # test adding a second time doesn't change anything
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id, role2.id]}

    # test removing
    item.operation_mode = 'remove'
    item.role_id = role2.id
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}

    # test removing a second time doesn't change anything
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}


def test_dispatch_auto(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    item = DispatchWorkflowStatusItem()
    item.role_key = '_receiver'
    item.dispatch_type = 'automatic'

    formdata = formdef.data_class()()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert not formdata.workflow_roles

    pub.role_class.wipe()
    role1 = pub.role_class('xxx1')
    role1.store()
    role2 = pub.role_class('xxx2')
    role2.store()

    for variable in ('form_var_foo', '{{form_var_foo}}'):
        formdata.data = {}
        formdata.workflow_roles = {}
        item.variable = variable
        item.rules = [
            {'role_id': role1.id, 'value': 'foo'},
            {'role_id': role2.id, 'value': 'bar'},
            {'role_id': role1.id, 'value': '42'},
        ]

        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        item.perform(formdata)
        assert not formdata.workflow_roles

        # no match
        formdata.data = {'1': 'XXX'}
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        item.perform(formdata)
        assert not formdata.workflow_roles

        # match
        formdata.data = {'1': 'foo'}
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        item.perform(formdata)
        assert formdata.workflow_roles == {'_receiver': [role1.id]}

        # other match
        formdata.data = {'1': 'bar'}
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        item.perform(formdata)
        assert formdata.workflow_roles == {'_receiver': [role2.id]}

        # integer match
        pub.substitutions.reset()
        # cannot store an integer in formdata.data, we mock substitutions:
        pub.substitutions.feed({'form_var_foo': 42})
        item.perform(formdata)
        assert formdata.workflow_roles == {'_receiver': [role1.id]}

    # unknown role
    formdata.data = {'1': 'foo'}
    formdata.workflow_roles = {}
    item.variable = variable
    item.rules = [
        {'role_id': 'foobar', 'value': 'foo'},
    ]
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert not formdata.workflow_roles
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.tech_id == '%s-_default-error-in-dispatch-missing-role-foobar' % formdef.id
    assert error.formdef_id == str(formdef.id)
    assert error.workflow_id == '_default'
    assert error.summary == 'error in dispatch, missing role (foobar)'
    assert error.occurences_count == 1


def test_dispatch_computed(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.slug = 'yyy'
    role.store()

    item = DispatchWorkflowStatusItem()

    formdata = formdef.data_class()()
    item.perform(formdata)
    assert not formdata.workflow_roles

    # with templates
    formdata = formdef.data_class()()
    item.role_key = '_receiver'
    item.role_id = '{{ "yyy" }}'  # slug
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}

    formdata = formdef.data_class()()
    item.role_key = '_receiver'
    item.role_id = '{{ "xxx" }}'  # name
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': [role.id]}

    # unknown role, with template
    LoggedError.wipe()
    formdata = formdef.data_class()()
    item.role_key = '_receiver'
    item.role_id = '{{ "foobar" }}'
    item.perform(formdata)
    assert not formdata.workflow_roles
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert (
        error.tech_id == '%s-_default-error-in-dispatch-missing-role-foobar-from-foobar-template' % formdef.id
    )
    assert error.formdef_id == str(formdef.id)
    assert error.workflow_id == '_default'
    assert error.summary == 'error in dispatch, missing role (foobar, from "{{ "foobar" }}" template)'
    assert error.occurences_count == 1


def test_dispatch_user(pub):
    pub.user_class.wipe()
    user = pub.user_class(name='foo')
    user.email = 'foo@localhost'
    user.name_identifiers = ['0123456789']
    user.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    item = DispatchWorkflowStatusItem()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    pub.substitutions.feed(formdata)

    item.role_key = '_receiver'
    item.role_id = '{{ form_user }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user.id]}

    formdata.workflow_roles = {}
    item.role_id = '{{ form_user_nameid }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user.id]}

    formdata.workflow_roles = {}
    item.role_id = '{{ form_user_email }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user.id]}

    formdata.workflow_roles = {}
    item.role_id = '{{ form_user_name }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user.id]}
    assert LoggedError.count() == 0

    formdata.workflow_roles = {}
    item.role_id = 'xyz'
    item.perform(formdata)
    assert formdata.workflow_roles == {}
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'error in dispatch, missing role (xyz)'

    LoggedError.wipe()
    formdata.workflow_roles = {}
    item.role_id = '{{ "foo@localhost,bar@localhost"|split:"," }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {}
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == 'error in dispatch, missing role ([\'foo@localhost\', \'bar@localhost\'], '
        'from "{{ "foo@localhost,bar@localhost"|split:"," }}" template)'
    )

    # do not dispatch to disabled users
    user.is_active = False
    user.store()
    LoggedError.wipe()
    formdata.workflow_roles = {}
    item.role_id = '{{ form_user_email }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {}
    assert (
        LoggedError.select()[0].summary
        == 'error in dispatch, missing role (foo@localhost, from "{{ form_user_email }}" template)'
    )

    user2 = pub.user_class(name='bar')
    user2.email = 'foo@localhost'
    user2.store()

    LoggedError.wipe()
    formdata.workflow_roles = {}
    item.role_id = '{{ form_user_email }}'
    item.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user2.id]}
