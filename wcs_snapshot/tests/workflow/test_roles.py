import pytest
import responses
from quixote import cleanup

from wcs import sessions
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.roles import AddRoleWorkflowStatusItem, RemoveRoleWorkflowStatusItem

from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_app_dir(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_roles(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    user = pub.user_class()
    user.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id

    item = AddRoleWorkflowStatusItem()

    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    item.role_id = str(role.id)
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [str(role.id)]

    # check django template
    user.roles = None
    user.store()
    item.role_id = '{{ "%s" }}' % role.id
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [str(role.id)]

    # tests for remove role action
    user.roles = None
    user.store()
    item = RemoveRoleWorkflowStatusItem()

    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    item.role_id = str(role.id)
    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    user.roles = [str(role.id)]
    user.store()
    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    user.roles = [str(role.id), '2']
    user.store()
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == ['2']


def test_add_remove_computed_roles(pub):
    user = pub.user_class()
    user.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id

    role = pub.role_class(name='plop')
    role.store()
    role2 = pub.role_class(name='xxx')
    role2.store()

    item = AddRoleWorkflowStatusItem()

    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    item.role_id = role.name
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [role.id]

    user.roles = None
    user.store()
    item = RemoveRoleWorkflowStatusItem()

    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    item.role_id = role.name
    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    user.roles = [role.id]
    user.store()
    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    user.roles = [role2.id, role.id]
    user.store()
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [role2.id]


def test_roles_idp(pub):
    pub.cfg['sp'] = {'idp-manage-user-attributes': True}
    pub.cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
    pub.write_cfg()
    user = pub.user_class()
    user.name_identifiers = ['xxx']
    user.store()

    role = pub.role_class(name='bar1')
    role.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id

    item = AddRoleWorkflowStatusItem()

    item.perform(formdata)
    assert not pub.user_class.get(user.id).roles
    with responses.RequestsMock() as rsps:
        pub.process_after_jobs()
        assert len(rsps.calls) == 0

    item.role_id = role.id
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [role.id]
    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/roles/bar1/members/xxx/', body=None, status=201)
        pub.process_after_jobs()
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url.startswith('http://idp.example.net/api/roles/bar1/members/xxx/')
        assert 'signature=' in rsps.calls[-1].request.url

    user.roles = None
    user.store()

    item2 = RemoveRoleWorkflowStatusItem()

    item2.perform(formdata)
    assert not pub.user_class.get(user.id).roles
    with responses.RequestsMock() as rsps:
        pub.process_after_jobs()
        assert len(rsps.calls) == 0

    item2.role_id = role.id
    user.roles = [role.id]
    user.store()
    item2.perform(formdata)
    assert not pub.user_class.get(user.id).roles
    with responses.RequestsMock() as rsps:
        rsps.delete('http://idp.example.net/api/roles/bar1/members/xxx/')
        pub.process_after_jobs()
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url.startswith('http://idp.example.net/api/roles/bar1/members/xxx/')
        assert 'signature=' in rsps.calls[-1].request.url

    # out of http request/response cycle
    pub._set_request(None)
    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/api/roles/bar1/members/xxx/', body=None, status=201)
        item.perform(formdata)
        assert pub.user_class.get(user.id).roles == [role.id]

    with responses.RequestsMock() as rsps:
        rsps.delete('http://idp.example.net/api/roles/bar1/members/xxx/')
        item2.perform(formdata)
        assert pub.user_class.get(user.id).roles == []


def test_roles_idp_clean_duplicates(pub):
    pub.role_class.wipe()

    pub.cfg['sp'] = {'idp-manage-user-attributes': True}
    pub.cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
    pub.write_cfg()
    user = pub.user_class()
    user.name_identifiers = ['xxx']
    user.store()

    role = pub.role_class(name='bar1')
    role.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id

    item = AddRoleWorkflowStatusItem()
    item.role_id = role.id
    item.perform(formdata)
    assert pub.user_class.get(user.id).roles == [role.id]
    item2 = RemoveRoleWorkflowStatusItem()
    item2.role_id = role.id
    item2.perform(formdata)
    assert not pub.user_class.get(user.id).roles

    assert len(pub.after_jobs) == 1

    with responses.RequestsMock() as rsps:
        rsps.delete('http://idp.example.net/api/roles/bar1/members/xxx/')
        pub.process_after_jobs()
        assert len(rsps.calls) == 1
