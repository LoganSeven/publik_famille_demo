import datetime

import pytest
import responses
from quixote import cleanup, get_publisher

from wcs.admin.settings import UserFieldsFormDef
from wcs.fields import DateField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.profile import UpdateUserProfileStatusItem
from wcs.workflows import Workflow

from ..backoffice_pages.test_all import create_user as create_backoffice_user
from ..backoffice_pages.test_all import login
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app


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
    pub.set_config(req)
    return pub


def test_profile(pub):
    LoggedError.wipe()

    User = pub.user_class
    user = User()
    user.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {'1': 'bar@localhost'}
    formdata.store()

    item = UpdateUserProfileStatusItem()
    formdata.data = {'1': 'Plop'}

    item.fields = [{'field_id': '__name', 'value': 'dj{{form_var_foo}}'}]
    item.perform(formdata)
    assert User.get(user.id).name == 'djPlop'
    item.fields = [{'field_id': '__name', 'value': 'ezt[form_var_foo]'}]
    item.perform(formdata)
    assert User.get(user.id).name == 'eztPlop'

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        StringField(id='3', label='test', varname='plop'),
        DateField(id='4', label='Date', varname='bar'),
    ]
    formdef.store()

    item.fields = [{'field_id': 'plop', 'value': '{{form_var_foo}}'}]
    item.perform(formdata)
    assert User.get(user.id).form_data.get('3') == 'Plop'
    assert not User.get(user.id).form_data.get('4')

    # check transmission to IdP
    get_publisher().cfg['sp'] = {'idp-manage-user-attributes': True}
    get_publisher().cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
    get_publisher().write_cfg()

    user = User.get(user.id)
    user.name_identifiers = ['xyz']
    user.store()

    for date_value in (
        '20/03/2018',
        '{{ "20/03/2018"|parse_date }}',
    ):
        # check local value
        item.fields = [{'field_id': 'bar', 'value': date_value}]
        item.perform(formdata)
        assert User.get(user.id).form_data.get('3') == 'Plop'
        assert User.get(user.id).form_data.get('4').tm_year == 2018

        with responses.RequestsMock() as rsps:
            rsps.patch('http://idp.example.net/api/users/xyz/')
            pub.process_after_jobs()
            assert len(rsps.calls) == 1
            assert rsps.calls[-1].request.body == '{"bar": "2018-03-20"}'

    assert LoggedError.count() == 0

    for date_value in ('baddate', '', {}, [], None):
        # reset date to a known value
        user.form_data['4'] = datetime.datetime.now().timetuple()
        user.store()
        year = User.get(user.id).form_data.get('4').tm_year
        # perform action
        item.fields = [{'field_id': 'bar', 'value': date_value}]
        item.perform(formdata)
        if date_value not in (None, ''):  # bad value : do nothing
            assert User.get(user.id).form_data.get('4').tm_year == year
        else:  # empty value : empty field
            assert User.get(user.id).form_data.get('4') is None

        with responses.RequestsMock() as rsps:
            rsps.patch('http://idp.example.net/api/users/xyz/')
            pub.process_after_jobs()
            assert len(rsps.calls) == 1
            if date_value not in (None, ''):  # bad value : do nothing
                assert rsps.calls[-1].request.body == '{}'
            else:  # empty value : null field
                assert rsps.calls[-1].request.body == '{"bar": null}'

    # out of http request/response cycle (cron, after_job)
    pub._set_request(None)
    item.fields = [{'field_id': 'bar', 'value': '01/01/2020'}]
    with responses.RequestsMock() as rsps:
        rsps.patch('http://idp.example.net/api/users/xyz/')
        item.perform(formdata)
        assert User.get(user.id).form_data.get('4').tm_year == 2020
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.body == '{"bar": "2020-01-01"}'

    # authentic error
    LoggedError.wipe()
    with responses.RequestsMock() as rsps:
        rsps.patch('http://idp.example.net/api/users/xyz/', status=404, json={'err': 1})
        item.perform(formdata)
        assert len(rsps.calls) == 1
        assert LoggedError.count() == 1
        error = LoggedError.select()[0]
        assert error.summary == 'Failed to update user profile on identity provider (404)'
        assert error.formdata_id == str(formdata.id)
        assert error.context['stack'] == [
            {'user': 'eztPlop', 'status': 404, 'user_uuid': 'xyz', 'response_data': '{"err": 1}'}
        ]


def test_profile_action_admin(pub):
    Workflow.wipe()

    create_backoffice_user(pub, is_admin=True)

    workflow = Workflow(name='test')
    st = workflow.add_status('st')
    action = st.add_action('update_user_profile')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(action.get_admin_url())
    assert resp.form['fields$element0$field_id'].options == [
        ('', True, ''),
        ('__email', False, 'Email'),
        ('__name', False, 'Name'),
    ]
    resp.form['fields$element0$field_id'] = '__email'
    resp.form['fields$element0$value$value_template'] = '{{ form_var_email }}'
    resp = resp.form.submit('fields$add_element')
    resp.form['fields$element1$field_id'] = '__name'
    resp.form['fields$element1$value$value_template'] = 'test'
    resp = resp.form.submit('submit')

    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].fields == [
        {'value': '{{ form_var_email }}', 'field_id': '__email'},
        {'value': 'test', 'field_id': '__name'},
    ]

    resp = app.get(action.get_admin_url())
    resp.form['fields$element0$field_id'] = ''
    resp = resp.form.submit('submit')
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].fields == [{'value': 'test', 'field_id': '__name'}]
