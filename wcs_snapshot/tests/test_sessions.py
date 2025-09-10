import datetime
import os
import pickle
import time

import pytest

from wcs import fields, sql
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


def teardown_module():
    clean_temporary_pub()


@pytest.fixture(scope='function')
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


@pytest.fixture
def http_request(pub):
    req = HTTPRequest(None, {})
    req.language = None
    pub._set_request(req)


@pytest.fixture
def user(pub):
    user = pub.user_class()
    user.email = 'foo@localhost'
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()
    return user


@pytest.fixture
def app(pub):
    return get_app(pub)


def test_session_max_age(pub, user, app):
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as cfg:
        cfg.write(
            '''[options]
session_max_age: 1
'''
        )
    pub.load_site_options()

    login(app, username='foo', password='foo')
    assert 'Logout' in app.get('/')
    time.sleep(0.5)
    assert 'Logout' in app.get('/')
    time.sleep(0.6)
    assert 'Logout' not in app.get('/')


def test_session_expire(pub, user, app):
    pub.session_manager.session_class.wipe()
    login(app, username='foo', password='foo')
    assert 'Logout' in app.get('/')
    session = pub.session_manager.session_class.select()[0]
    session.set_expire(time.time() + 10)
    session.store()
    assert 'Logout' in app.get('/')
    session.set_expire(time.time() - 1)
    session.store()
    assert 'Logout' not in app.get('/')


def test_sessions_visiting_objects(pub, http_request):
    # check it starts with nothing
    assert len(pub.session_class.get_visited_objects()) == 0

    class MockFormData:
        def __init__(self, id):
            self.id = id

        def get_object_key(self):
            return 'formdata-foobar-%s' % self.id

    # mark two visits
    session1 = pub.session_class(id='session1')
    session1.user = 'FOO'
    session1.mark_visited_object(MockFormData(1))
    session1.mark_visited_object(MockFormData(2))
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 2
    assert {x[0] for x in pub.session_class.get_object_visitors(MockFormData(2))} == {'FOO'}

    # mark a visit as being in the past
    session1.visiting_objects['formdata-foobar-1'] = time.time() - 35 * 60
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 1

    # check older visits are automatically removed
    session1 = pub.session_class.get('session1')
    assert len(session1.visiting_objects.keys()) == 2
    session1.mark_visited_object(MockFormData(2))
    assert len(session1.visiting_objects.keys()) == 1
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 1
    assert list(pub.session_class.get_visited_objects()) == ['formdata-foobar-2']

    # check with a second session
    session1.mark_visited_object(MockFormData(1))
    session1.mark_visited_object(MockFormData(2))
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 2

    # mark a visit as being in the past
    session1.visiting_objects['formdata-foobar-1'] = time.time() - 35 * 60
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 1

    # check older visits are automatically removed
    session1 = pub.session_class.get('session1')
    assert len(session1.visiting_objects.keys()) == 2
    session1.mark_visited_object(MockFormData(2))
    assert len(session1.visiting_objects.keys()) == 1
    session1.store()
    assert len(pub.session_class.get_visited_objects()) == 1
    assert list(pub.session_class.get_visited_objects()) == ['formdata-foobar-2']

    # check with a second session
    session2 = pub.session_class(id='session2')
    session2.user = 'BAR'
    session2.store()
    assert len(pub.session_class.get_visited_objects()) == 1
    session2.mark_visited_object(MockFormData(2))
    session2.store()
    assert len(pub.session_class.get_visited_objects()) == 1
    session2.mark_visited_object(MockFormData(3))
    session2.store()
    assert len(pub.session_class.get_visited_objects()) == 2

    assert list(pub.session_class.get_visited_objects(exclude_user='BAR')) == ['formdata-foobar-2']

    # check visitors
    assert {x[0] for x in pub.session_class.get_object_visitors(MockFormData(2))} == {'FOO', 'BAR'}
    assert {x[0] for x in pub.session_class.get_object_visitors(MockFormData(1))} == set()


def test_session_do_not_reuse_id(pub, user, app):
    pub.session_manager.session_class.wipe()
    login(app, username='foo', password='foo')
    assert pub.session_manager.session_class.count() == 1
    resp = app.get('/')
    login_page = app.get('/login/')
    login_form = login_page.forms['login-form']
    login_form['username'] = 'foo'
    login_form['password'] = 'foo'
    resp = login_form.submit()
    assert resp.status_int == 302
    assert pub.session_manager.session_class.count() == 2


def test_session_substitution_variables_1st_page_condition(pub, user, app):
    pub.session_manager.session_class.wipe()
    resp = app.get('/')
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st PAGE',
            condition={'type': 'django', 'value': 'session_hash_id'},
        ),
        fields.CommentField(id='10', label='COMHASH1 [session_hash_id]'),
        fields.PageField(id='8', label='2nd PAGE'),
        fields.CommentField(id='9', label='COM2 [session_hash_id]'),
    ]
    formdef.store()

    resp = app.get('/foobar/')
    assert pub.session_manager.session_class.count() == 1
    session = pub.session_manager.session_class.select()[0]
    assert 'COMHASH1 %s' % session.get_substitution_variables().get('session_hash_id') in resp.text


def test_session_clean_job(pub, user, app, freezer):
    pub.session_manager.session_class.wipe()
    login(app, username='foo', password='foo')
    assert pub.session_manager.session_class.count() == 1
    pub.clean_sessions()
    assert pub.session_manager.session_class.count() == 1
    freezer.move_to(datetime.datetime.now() + datetime.timedelta(2))
    pub.clean_sessions()
    assert pub.session_manager.session_class.count() == 1
    freezer.move_to(datetime.datetime.now() + datetime.timedelta(5))  # last usage limit
    pub.clean_sessions()
    assert pub.session_manager.session_class.count() == 0


def test_message(pub, user, app):
    login(app, username='foo', password='foo')
    session = pub.session_manager.session_class.select()[0]
    session.add_message('message')
    assert '<li class="error">message</li>' in str(session.display_message())
    session.add_message('"message with a job"', job_id='uid', level='info')
    message = str(session.display_message())
    assert '<li data-job="uid" class="info">&quot;message with a job&quot;</li>' in message


def test_inactive_user(pub, user, app):
    login(app, username='foo', password='foo')
    assert 'Logout' in app.get('/')
    user.is_active = False
    user.store()
    assert 'Logout' not in app.get('/')


def test_transient_data_removal(pub, app):
    pub.session_manager.session_class.wipe()
    sql.TransientData.wipe()
    FormDef.wipe()
    resp = app.get('/')

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id='1', label='string')]
    formdef.store()
    formdef.data_class().wipe()

    resp = app.get('/foobar/')
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')

    assert sql.Session.count() == 1
    assert sql.TransientData.count() == 2
    transient_data1, transient_data2 = sql.TransientData.select(order_by='last_update_time')
    assert transient_data1.data is None  # form_token
    assert transient_data2.data == {'1': 'test'}  # magictoken

    app.get('/logout')
    assert sql.Session.count() == 0
    assert sql.TransientData.count() == 0

    transient_data2.store()  # session_id not found, should not fail


def test_magictoken_migration(pub, app):
    pub.session_manager.session_class.wipe()
    sql.TransientData.wipe()
    FormDef.wipe()
    resp = app.get('/')

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.PageField(id='0', label='1st PAGE'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd PAGE'),
        fields.PageField(id='3', label='3rd PAGE'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = app.get('/foobar/')
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')

    # migrate back session to look like before transient data table
    assert pub.session_manager.session_class.count() == 1
    session = pub.session_manager.session_class.select()[0]
    session.magictokens = {}
    for transient_data in sql.TransientData.select():
        session.magictokens[transient_data.id] = transient_data.data
    sql.TransientData.wipe()

    _, cur = sql.get_connection_and_cursor()
    sql_statement = 'UPDATE sessions SET session_data = %s WHERE id = %s'
    cur.execute(sql_statement, (bytearray(pickle.dumps(session.__dict__, protocol=2)), session.id))
    cur.close()

    # and get back to submitting form, it should run migration
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'test'


def test_jsonp_display_value(pub, http_request):
    session = pub.session_class(id='session')
    for i in range(25):
        session.set_jsonp_display_value(str(i), i)
    assert {x: y[1] for x, y in session.jsonp_display_values.items()} == {str(i): i for i in range(5, 25)}
