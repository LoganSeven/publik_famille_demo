import hashlib
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zoneinfo
from unittest import mock

import pytest
import responses
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from webtest import Hidden, Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.forms.root import PublicFormStatusPage
from wcs.logged_errors import LoggedError
from wcs.qommon.emails import docutils
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.template import Template
from wcs.roles import logged_users_role
from wcs.sql import TransientData
from wcs.sql_criterias import Equal, NotEqual
from wcs.tracking_code import TrackingCode
from wcs.wf.create_formdata import JournalAssignationErrorPart, Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.wf.wscall import JournalWsCallErrorPart
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import (
    JumpEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowVariablesFieldsFormDef,
)

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


def assert_equal_zip(stream1, stream2):
    with zipfile.ZipFile(stream1) as z1, zipfile.ZipFile(stream2) as z2:
        assert set(z1.namelist()) == set(z2.namelist())
        for name in z1.namelist():
            if name == 'styles.xml':
                continue
            if name in ['content.xml', 'meta.xml']:
                t1, t2 = ET.tostring(ET.XML(z1.read(name))), ET.tostring(ET.XML(z2.read(name)))
                try:
                    # >= python 3.8: tostring preserves attribute order; use canonicalize to sort them
                    t1, t2 = ET.canonicalize(t1), ET.canonicalize(t2)
                except AttributeError:
                    pass
            else:
                t1, t2 = z1.read(name), z2.read(name)
            assert t1 == t2, 'file "%s" differs' % name


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub(lazy_mode=bool('lazy' in request.param))
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['users'] = {
        'field_phone': '_phone',
    }
    pub.write_cfg()

    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub.set_app_dir(req)

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='_phone', label='phone', varname='phone', validation={'type': 'phone'})
    ]
    formdef.store()

    Category.wipe()
    cat = Category(name='foobar')
    cat.store()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def create_formdef():
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    return formdef


def create_user(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()

    user = pub.user_class()
    user.name = 'User Name'
    user.email = 'foo@localhost'
    user.form_data = {'_phone': '+33123456789'}
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()
    return user


def create_user_and_admin(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()

    user = pub.user_class()
    user.email = 'foo@localhost'
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()

    admin = pub.user_class()
    admin.email = 'admin@localhost'
    admin.is_admin = True
    admin.store()
    account = PasswordAccount(id='admin')
    account.set_password('admin')
    account.user_id = admin.id
    account.store()
    return user, admin


def get_displayed_tracking_code(resp):
    return resp.pyquery('a[name="tracking-code-display"]').text()


def test_home(pub):
    create_formdef()
    home = get_app(pub).get('/')
    assert 'category-misc' in home.text
    assert '<a class="" href="test/">test</a>' in home.text


def test_home_with_user_forms(pub):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.category_id = Category.get_by_slug('foobar').id
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.store()
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.status = 'wf-st1'
    formdata.data = {}
    formdata.store()
    draft = formdef.data_class()()
    draft.user_id = user.id
    draft.status = 'draft'
    draft.data = {}
    draft.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/')
    assert 'Your Current Forms' not in resp
    assert 'Your Past Forms' in resp
    assert '<a href="/test/%s/"' % formdata.id in resp
    assert 'Draft' in resp
    assert '<a href="/test/%s/"' % draft.id in resp
    resp = app.get('/test/%s' % formdata.id)
    assert resp.location == 'http://example.net/test/1/'
    resp = app.get('/test/%s/' % draft.id, status=302)
    assert resp.location.startswith('http://example.net/foobar/test/%s/' % draft.id)
    resp = resp.follow(status=302)
    assert resp.location.startswith('http://example.net/foobar/test/?mt=')
    resp = resp.follow(status=200)

    # add action -> pending
    st1.add_action('choice')
    wf.store()

    resp = app.get('/')
    assert 'Your Current Forms' in resp
    assert 'Your Past Forms' not in resp
    assert '<a href="/test/%s/"' % formdata.id in resp

    # disable formdef: formdatas are still visible and accessible, drafts are not
    formdef.disabled = True
    formdef.store()
    resp = app.get('/')
    assert 'Your Current Forms' in resp
    assert 'Your Past Forms' not in resp
    assert '<a href="/test/%s/"' % formdata.id in resp
    assert 'Draft' not in resp
    assert '<a href="test/%s"' % draft.id not in resp
    resp = app.get('/test/%s/' % draft.id, status=302)
    assert resp.location.startswith('http://example.net/foobar/test/%s/' % draft.id)
    resp = resp.follow(status=302)
    assert resp.location.startswith('http://example.net/foobar/test/?mt=')
    resp = resp.follow(status=403)


def test_home_category(pub):
    cat = Category.get_by_slug('foobar')
    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.store()
    home = get_app(pub).get('/')
    assert 'category-foobar' in home.text
    assert 'category-misc' not in home.text
    assert '<a class="" href="foobar/test/">test</a>' in home.text


def test_home_two_categories(pub):
    cat = Category.get_by_slug('foobar')
    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'foobar'
    formdef2.fields = []
    formdef2.store()

    resp = get_app(pub).get('/')
    assert 'category-foobar' in resp.text  # 1st formdef
    assert 'category-misc' in resp.text  # 2nd formdef, fake category
    assert '<a class="" href="foobar/test/">test</a>' in resp.text

    cat2 = Category(name='barfoo')
    cat2.store()
    formdef2.category_id = cat2.id
    formdef2.store()

    resp = get_app(pub).get('/')
    assert 'category-foobar' in resp.text  # 1st formdef
    assert 'category-barfoo' in resp.text  # 2nd formdef
    assert 'category-misc' not in resp.text  # no more "misc" category


def test_home_keywords(pub):
    cat = Category.get_by_slug('foobar')
    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.keywords = 'hello, world'
    formdef.store()
    home = get_app(pub).get('/')
    assert home.html.find('div', {'data-keywords': 'hello world'}) or home.html.find(
        'div', {'data-keywords': 'world hello'}
    )
    assert home.html.find('li', {'data-keywords': 'hello world'}) or home.html.find(
        'li', {'data-keywords': 'world hello'}
    )


def test_home_formdef_description(pub):
    formdef = create_formdef()
    formdef.description = 'HELLO WORLD'
    formdef.store()
    home = get_app(pub).get('/')
    assert 'HELLO WORLD' in home.text
    assert '<a class="" href="test/">test</a>' in home.text


def test_home_disabled(pub):
    formdef = create_formdef()
    formdef.disabled = True
    formdef.store()
    home = get_app(pub).get('/')
    assert '<a href="test/">test</a>' not in home.text

    # check access is denied
    get_app(pub).get('/test/', status=403)


def test_home_disabled_with_redirect(pub):
    formdef = create_formdef()
    formdef.disabled = True
    formdef.disabled_redirection = 'http://example.org'
    formdef.store()
    resp = get_app(pub).get('/')
    assert '<a class="redirection" href="test/">test</a>' in resp.text
    resp = resp.click('test')
    assert resp.location == 'http://example.org'


def test_home_inaccessible(pub):
    formdef = create_formdef()
    formdef.roles = ['xxx']
    formdef.store()
    home = get_app(pub).get('/')
    assert home.status_int == 302
    assert home.location == 'http://example.net/login/?next=http%3A%2F%2Fexample.net%2F'


def test_home_always_advertise(pub):
    formdef = create_formdef()
    formdef.roles = ['xxx']
    formdef.always_advertise = True
    formdef.store()
    home = get_app(pub).get('/')
    assert '<a href="test/">test</a>' in home.text
    assert '<a href="test/">test</a><span> (authentication required)</span>' in home.text


def test_home_redirect(pub):
    pub.cfg['misc']['homepage-redirect-url'] = 'http://www.example.com/'
    pub.write_cfg()
    create_formdef()
    home = get_app(pub).get('/')
    assert home.status_int == 302
    assert home.location == 'http://www.example.com/'


def test_home_redirect_var(pub):
    pub.cfg['misc']['homepage-redirect-url'] = 'http://www.example.com/[site_lang]/'
    pub.write_cfg()
    create_formdef()
    home = get_app(pub).get('/')
    assert home.status_int == 302
    assert home.location == 'http://www.example.com/en/'


def test_category_page(pub):
    formdef = create_formdef()
    formdef.category_id = '1'
    formdef.store()
    resp = get_app(pub).get('/foobar/', status=302)
    assert resp.location == 'http://example.net/'


def test_category_page_redirect(pub):
    cat = Category.get_by_slug('foobar')
    cat.redirect_url = 'http://www.example.com/'
    cat.store()
    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.store()
    resp = get_app(pub).get('/foobar/')
    assert resp.status_int == 302
    assert resp.location == 'http://www.example.com/'


def test_category_page_redirect_var(pub):
    cat = Category.get_by_slug('foobar')
    cat.redirect_url = 'http://www.example.com/[site_lang]/[category_slug]/'
    cat.store()
    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.store()
    resp = get_app(pub).get('/foobar/')
    assert resp.status_int == 302
    assert resp.location == 'http://www.example.com/en/foobar/'


def test_form_access(pub):
    formdef = create_formdef()
    get_app(pub).get('/test/', status=200)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    # check a formdef protected by a role cannot be accessed
    formdef.roles = [role.id]
    formdef.store()
    # an unlogged user will ge ta redirect to login
    resp = get_app(pub).get('/test/', status=302)
    assert '/login' in resp.location

    # while a logged-in user will get a 403
    user = create_user(pub)
    login(get_app(pub), username='foo', password='foo').get('/test/', status=403)

    # unless the user has the right role
    user = create_user(pub)
    user.roles = [role.id]
    user.store()
    login(get_app(pub), username='foo', password='foo').get('/test/', status=200)

    # check admin has access, even without specific roles
    user = create_user(pub)
    user.roles = []
    user.is_admin = True
    user.store()
    login(get_app(pub), username='foo', password='foo').get('/test/', status=200)

    # check special "logged users" role
    formdef.roles = [logged_users_role().id]
    formdef.store()
    user = create_user(pub)
    login(get_app(pub), username='foo', password='foo').get('/test/', status=200)
    resp = get_app(pub).get('/test/', status=302)  # redirect to login

    # check "receiver" can also access the formdef
    formdef = create_formdef()
    formdef.roles = [-2]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    user = create_user(pub)
    user.roles = [role.id]
    user.store()
    login(get_app(pub), username='foo', password='foo').get('/test/', status=200)


def test_form_access_auth_context(pub):
    create_user(pub)

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'auth-contexts', 'fedict')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    formdef = create_formdef()
    get_app(pub).get('/test/', status=200)

    formdef.required_authentication_contexts = ['fedict']
    formdef.roles = [logged_users_role().id]
    formdef.store()

    # an unlogged user will get a redirect to login
    resp = get_app(pub).get('/test/', status=302)
    assert '/login' in resp.location

    # a user logged in with a simple username/password tuple will get a page
    # to relogin with a stronger auth
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert 'You need a stronger authentication level to fill this form.' in resp.text

    for session in pub.session_manager.values():
        session.saml_authn_context = 'urn:oasis:names:tc:SAML:2.0:ac:classes:SmartcardPKI'
        session.store()
    resp = app.get('/test/')
    assert 'You need a stronger authentication level to fill this form.' not in resp.text
    assert resp.form


def test_form_invalid_id(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()

    get_app(pub).get('/test/123', status=404)
    get_app(pub).get('/test/abc', status=404)
    get_app(pub).get('/test/12_345', status=404)
    get_app(pub).get(f'/test/{2**31+5}', status=404)


def test_form_cancelurl(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()

    # path
    resp = get_app(pub).get('/test/?cancelurl=/plop/')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/plop/'

    # full URL
    resp = get_app(pub).get('/test/?cancelurl=http://example.net/plop/')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/plop/'

    # remote site
    get_app(pub).get('/test/?cancelurl=http://example.org/plop/', status=400)

    # javascript
    get_app(pub).get('/test/?cancelurl=javascript:alert("hello")', status=400)

    pub.site_options.add_section('api-secrets')
    pub.site_options.set('api-secrets', 'example.org', 'xyz')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = get_app(pub).get('/test/?cancelurl=http://example.org/plop/')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.org/plop/'

    pub.site_options.remove_section('api-secrets')
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    get_app(pub).get('/test/?cancelurl=http://example.org/plop/', status=400)

    pub.site_options.set('options', 'relatable-hosts', 'example.com')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    get_app(pub).get('/test/?cancelurl=http://example.org/plop/', status=400)

    pub.site_options.set('options', 'relatable-hosts', 'example.com, example.org')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = get_app(pub).get('/test/?cancelurl=http://example.org/plop/')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.org/plop/'

    # repeated param, second one is ignored
    resp = get_app(pub).get('/test/?cancelurl=/plop/&cancelurl=/plip/')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/plop/'


def test_form_submit(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    page = get_app(pub).get('/test/')
    next_page = page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert 'None' not in next_page.text
    assert formdef.data_class().count() == 1
    assert next_page.pyquery('#summary').attr['class'] == 'section foldable folded'
    assert next_page.pyquery('#summary .disclose-message')
    assert formdef.data_class().select()[0].submission_context['language'] == 'en'
    assert formdef.data_class().select()[0].workflow_data['_source_ip']


def test_form_submit_no_confirmation(pub):
    formdef = create_formdef()
    formdef.confirmation = False
    formdef.store()
    page = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    next_page = page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert formdef.data_class().count() == 1


def test_form_string_field_submit(pub):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.store()
    page = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    next_page = page.forms[0].submit('submit')  # but the field is required
    assert next_page.pyquery('#form_error_f0').text() == 'required field'
    next_page.forms[0]['f0'] = 'foobar'
    next_page = next_page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'0': 'foobar'}


def test_form_string_with_invalid_xml_chars(pub):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.form['f0'] = 'hello\x0b\x0cworld'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    data = formdef.data_class().select()[0]
    assert data.data == {'0': 'helloworld'}


def test_form_submit_handling_role_info(pub):
    role = pub.role_class(name='xxx')
    role.details = 'Managing service'
    role.store()
    formdef = create_formdef()
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp
    assert 'Your case is handled by' in resp
    assert 'Managing service' in resp

    formdata = formdef.data_class().select()[0]
    formdata.jump_status('rejected')
    formdata.store()
    resp = resp.test_app.get(resp.request.url)
    assert 'Your case has been handled by' in resp


def assert_current_page(resp, page_label):
    assert resp.pyquery('.wcs-step.current .wcs-step--label-text').text() == page_label


def test_form_multi_page(pub):
    for initial_condition in (None, 'True'):
        formdef = create_formdef()
        formdef.fields = [
            fields.PageField(id='0', label='1st page'),
            fields.StringField(id='1', label='string'),
            fields.PageField(id='2', label='2nd page'),
            fields.StringField(id='3', label='string 2'),
        ]
        formdef.fields[0].condition = {'type': 'django', 'value': initial_condition}
        formdef.store()
        page = get_app(pub).get('/test/')
        formdef.data_class().wipe()
        page.forms[0]['f1'] = 'foo'
        assert page.forms[0].fields['submit'][0].value_if_submitted() == 'Next'
        next_page = page.forms[0].submit('submit')
        assert_current_page(next_page, '2nd page')
        assert next_page.forms[0]['previous']
        next_page.forms[0]['f3'] = 'bar'
        next_page = next_page.forms[0].submit('submit')
        assert_current_page(next_page, 'Validating')
        assert 'Check values then click submit.' in next_page.text
        next_page = next_page.forms[0].submit('submit')
        assert next_page.status_int == 302
        next_page = next_page.follow()
        assert 'The form has been recorded' in next_page.text
        assert formdef.data_class().count() == 1
        data_id = formdef.data_class().select()[0].id
        data = formdef.data_class().get(data_id)
        assert data.data == {'1': 'foo', '3': 'bar'}


def test_form_multi_page_title_and_subtitle_as_template(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.TitleField(id='4', label='<i>title of second page {{ form_var_foo }}</i>'),
        fields.SubtitleField(id='5', label='<i>subtitle of second page {{ form_var_foo }}</i>'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.form['f1'] = '35 < 42'
    resp = resp.form.submit('submit')
    resp.form['f3'] = 'bar'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    expected_label = '&lt;i&gt;title of second page 35 &lt; 42&lt;/i&gt;'
    assert '<div class="title "><h3>%s</h3></div>' % expected_label in resp.text
    expected_label = '&lt;i&gt;subtitle of second page 35 &lt; 42&lt;/i&gt;'
    assert '<div class="subtitle "><h4>%s</h4></div>' % expected_label in resp.text


def test_form_multi_page_condition(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page', condition={'type': 'django', 'value': 'False'}),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text
    assert resp.forms[0]['previous']
    resp = resp.forms[0].submit('previous')
    assert resp.forms[0]['f1']


def test_form_multi_page_condition_select(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(id='1', label='select', required='required', varname='foo', items=['Foo', 'Bar']),
        fields.PageField(
            id='2', label='2nd page', condition={'type': 'django', 'value': 'form_var_foo == "Foo"'}
        ),
        fields.PageField(
            id='3', label='3rd page', condition={'type': 'django', 'value': 'form_var_foo == "Bar"'}
        ),
        fields.StringField(id='4', label='string 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert '2nd page' not in resp.text
    assert '3rd page' not in resp.text
    assert resp.forms[0]['f1'].value == 'Foo'  # preset
    resp = resp.forms[0].submit('submit')
    assert '2nd page' in resp.text
    assert '3rd page' not in resp.text
    assert_current_page(resp, '2nd page')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'Bar'
    resp = resp.forms[0].submit('submit')
    assert '2nd page' not in resp.text
    assert '3rd page' in resp.text
    assert_current_page(resp, '3rd page')


def test_form_multi_page_condition_select_new_varname(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(id='1', label='select', required='required', varname='foo', items=['Foo', 'Bar']),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_foo == "Foo"'},
        ),
        fields.PageField(
            id='3',
            label='3rd page',
            condition={'type': 'django', 'value': 'form_var_foo == "Bar"'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert '2nd page' not in resp.text
    assert '3rd page' not in resp.text
    resp.forms[0]['f1'] = 'Foo'
    resp = resp.forms[0].submit('submit')
    assert '2nd page' in resp.text
    assert '3rd page' not in resp.text
    assert_current_page(resp, '2nd page')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'Bar'
    resp = resp.forms[0].submit('submit')
    assert '2nd page' not in resp.text
    assert '3rd page' in resp.text
    assert_current_page(resp, '3rd page')


def test_form_multi_page_condition_checkbox(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BoolField(id='1', label='checkbox', varname='checkbox'),
        fields.PageField(
            id='2',
            condition={'type': 'django', 'value': 'form_var_checkbox == "False"'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'].checked = True
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text
    assert resp.forms[0]['previous']
    resp = resp.forms[0].submit('previous')
    assert resp.forms[0]['f1']
    resp.forms[0]['f1'].checked = False
    resp = resp.forms[0].submit('submit')  # should go to second page
    assert 'f3' in resp.forms[0].fields


def test_form_multi_page_condition_json_check(pub):
    # make sure the json export has no value for fields from hidden pages
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BoolField(id='1', label='checkbox', varname='checkbox'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_checkbox == "False"'},
        ),
        fields.StringField(id='3', label='string 2', varname='st2'),
        fields.PageField(
            id='4',
            label='3rd page',
            condition={'type': 'django', 'value': 'form_var_checkbox == "True"'},
        ),
        fields.StringField(id='5', label='string 3', varname='st3'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.form['f1'].checked = True
    resp = resp.form.submit('submit')  # should go straight to 3rd page
    assert 'f5' in resp.form.fields
    resp.form['f5'] = 'VALUE F5'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['previous']
    resp = resp.form.submit('previous')
    resp = resp.form.submit('previous')

    # back to first page
    assert 'f1' in resp.form.fields
    resp.form['f1'].checked = False
    resp = resp.form.submit('submit')  # should go to second page
    assert 'f3' in resp.form.fields
    resp.form['f3'] = 'VALUE F3'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    resp = resp.form.submit()

    assert len(formdef.data_class().select()) == 1
    json_dict = formdef.data_class().select()[0].get_json_export_dict()
    assert json_dict['fields']['st2'] == 'VALUE F3'
    assert json_dict['fields']['st3'] is None


def test_form_multi_page_condition_no_confirmation_json_check(pub):
    # same as above but without the confirmation page.
    formdef = create_formdef()
    formdef.confirmation = False
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BoolField(id='1', label='checkbox', varname='checkbox'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_checkbox == "False"'},
        ),
        fields.StringField(id='3', label='string 2', varname='st2'),
        fields.PageField(
            id='4',
            label='3rd page',
            condition={'type': 'django', 'value': 'form_var_checkbox == "True"'},
        ),
        fields.StringField(id='5', label='string 3', varname='st3'),
        fields.PageField(id='6', label='4th page'),
        fields.CommentField(id='7', label='Check values then click submit.'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.form['f1'].checked = True
    resp = resp.form.submit('submit')  # should go straight to 3rd page
    assert 'f5' in resp.form.fields
    resp.form['f5'] = 'VALUE F5'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['previous']
    resp = resp.form.submit('previous')
    resp = resp.form.submit('previous')

    # back to first page
    assert 'f1' in resp.form.fields
    resp.form['f1'].checked = False
    resp = resp.form.submit('submit')  # should go to second page
    assert 'f3' in resp.form.fields
    resp.form['f3'] = 'VALUE F3'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    resp = resp.form.submit('submit')

    assert len(formdef.data_class().select()) == 1
    json_dict = formdef.data_class().select()[0].get_json_export_dict()
    assert json_dict['fields']['st2'] == 'VALUE F3'
    assert json_dict['fields']['st3'] is None


def test_form_multi_page_condition_data_source(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BoolField(id='1', label='checkbox', varname='checkbox'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'data_source.foobar|length > 0'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    # add the named data source, empty
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()

    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text
    assert resp.forms[0]['previous']
    resp = resp.forms[0].submit('previous')
    assert resp.forms[0]['f1']

    # replace the named data source with one with items
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "un", "text": "un"}, {"id": "deux", "text": "deux"}]',
    }
    data_source.store()

    resp = resp.forms[0].submit('submit')  # should go to second page
    assert 'f3' in resp.forms[0].fields


def test_form_multi_page_condition_data_source_with_form_variable(pub):
    # this tries to recreate #8272 which is about a json datasource being
    # used in a page condition and taking a value from the given page to
    # filter its content.  It is emulated here with a jsonvalue datasource
    # being empty if a field was not set.
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='xxx', required='optional'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'data_source.foobar|length > 0'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    # add the named data source, related to a field on the first page
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': '[{% if form_var_xxx %}{"id": "1", "text": "{{ form_var_xxx }}"}{% endif %}]',
    }
    data_source.store()

    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text
    assert resp.forms[0]['previous']
    resp = resp.forms[0].submit('previous')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'HELLO'
    resp = resp.forms[0].submit('submit')  # should go to second page
    assert 'f3' in resp.forms[0].fields


def test_form_multi_page_condition_on_first_page(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page', condition={'type': 'django', 'value': 'False'}),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    # should be on second page already
    assert resp.pyquery('.buttons button.form-previous[hidden][disabled]')
    resp.form['f3'] = 'foo'
    assert_current_page(resp, '2nd page')
    resp = resp.form.submit('submit')  # -> 3rd page
    assert_current_page(resp, '3rd page')
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.form['previous']
    resp = resp.form.submit('previous')  # -> 3rd page
    assert_current_page(resp, '3rd page')
    resp = resp.form.submit('previous')  # -> 2nd page
    assert_current_page(resp, '2nd page')
    assert resp.form['f3']
    assert resp.pyquery('.buttons button.form-previous[hidden][disabled]')


def test_form_multi_page_condition_on_first_and_next(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page', condition={'type': 'django', 'value': 'True'}),
        fields.StringField(id='1', label='string', varname='val1'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_val1 == "foo"'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.form.submit('submit')
    assert resp.form['f3']
    resp.form['f3'] = 'bar'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    assert len(formdef.data_class().select()) == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'1': 'foo', '3': 'bar'}

    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'xxx'
    resp = resp.form.submit('submit')
    with pytest.raises(AssertionError):
        assert resp.form['f3']
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    assert len(formdef.data_class().select()) == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data.get('1') == 'xxx'
    assert data.data.get('3') is None


def test_form_multi_page_condition_no_visible_page(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(
            id='0', label='1st page', condition={'type': 'django', 'value': 'form_var_foo != "foo"'}
        ),
        fields.StringField(id='1', label='string', required='optional', varname='foo'),
        fields.PageField(id='2', label='2nd page', condition={'type': 'django', 'value': 'False'}),
        fields.StringField(id='3', label='string 2', required='optional'),
    ]
    formdef.store()

    # 1. formdef with no visible page after an initial page was shown
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.wcs-step--label-text').text() == 'Validating'

    # 2. no page after a page was shown (typically pages conditioned on a webservice that went down)
    resp = get_app(pub).get('/test/')
    formdef.fields[0].condition['value'] = 'False'
    formdef.store()
    resp = resp.form.submit('submit')
    assert 'error-page' in resp
    assert 'This form has no visible page.' in resp

    # 3. no page straight away
    resp = get_app(pub).get('/test/')
    assert 'error-page' in resp
    assert 'This form has no visible page.' in resp


def test_form_multi_page_condition_on_past_page(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.PageField(
            id='2', label='2nd page', condition={'type': 'django', 'value': 'form_var_foo == "foo"'}
        ),
        fields.PageField(
            id='3', label='3rd page', condition={'type': 'django', 'value': 'form_var_foo == "foo"'}
        ),
        fields.PageField(id='4', label='4th page'),
        fields.StringField(id='555', label='string', required='optional', varname='foo'),
        fields.PageField(id='5', label='5th page'),
    ]
    formdef.store()

    # past pages appearing
    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')  # -> 4th page
    resp.form['f555'] = 'foo'
    resp = resp.form.submit('submit')  # next page, so 5th page
    assert resp.pyquery('.wcs-step.current .wcs-step--label-text').text() == '5th page'

    # page pages disappearing
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.PageField(
            id='2', label='2nd page', condition={'type': 'django', 'value': 'form_var_foo != "foo"'}
        ),
        fields.PageField(
            id='3', label='3rd page', condition={'type': 'django', 'value': 'form_var_foo != "foo"'}
        ),
        fields.PageField(id='4', label='4th page'),
        fields.StringField(id='555', label='string', required='optional', varname='foo'),
        fields.PageField(id='5', label='5th page'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('submit')  # -> 4th page
    resp.form['f555'] = 'foo'
    resp = resp.form.submit('submit')  # next page, so 5th page
    assert resp.pyquery('.wcs-step.current .wcs-step--label-text').text() == '5th page'


def test_form_multi_page_many_conditions(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='0', label='2nd page', condition={'type': 'django', 'value': 'True'}),
    ]

    formdef.store()
    formdef.data_class().wipe()

    with mock.patch('wcs.qommon.publisher.Substitutions.invalidate_cache') as invalidate_cache:
        get_app(pub).get('/test/')
        call_count = invalidate_cache.call_count

    for i in range(30):
        formdef.fields.append(
            fields.PageField(
                id=str(i + 2),
                label='page %s' % (i + 2),
                condition={'type': 'django', 'value': 'True'},
            )
        )
    formdef.store()

    # check the cache doesn't get invalidated for every page
    with mock.patch('wcs.qommon.publisher.Substitutions.invalidate_cache') as invalidate_cache:
        get_app(pub).get('/test/')
        assert invalidate_cache.call_count <= call_count


def test_form_multi_page_condition_stored_values(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_foo == "toto"'},
        ),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'toto'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'BAR'
    resp = resp.form.submit('submit')  # -> page 3
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert 'BAR' in resp.text
    resp = resp.form.submit('previous')  # -> page 3
    resp = resp.form.submit('previous')  # -> page 2
    resp = resp.form.submit('previous')  # -> page 1
    resp.form['f1'] = 'blah'
    resp = resp.form.submit('submit')  # -> page 3
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert 'BAR' not in resp.text
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'blah'
    assert formdata.data.get('3') is None

    # same without validation page
    formdef.confirmation = False
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'toto'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'BAR'
    resp = resp.form.submit('submit')  # -> page 3
    resp = resp.form.submit('previous')  # -> page 2
    resp = resp.form.submit('previous')  # -> page 1
    resp.form['f1'] = 'blah'
    resp = resp.form.submit('submit')  # -> page 3
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'blah'
    assert formdata.data.get('3') is None


def test_form_multi_page_post_conditions(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page', condition={'type': 'django', 'value': 'False'}),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3', varname='bar'),
    ]

    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].data['1'] == 'foo'
    assert formdef.data_class().select()[0].data['5'] == 'bar'

    formdef.fields[4].post_conditions = [
        {'condition': {'type': 'django', 'value': 'False'}, 'error_message': 'You shall not pass.'},
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'errornotice' in resp.text
    assert 'global-errors' in resp.text
    assert 'You shall not pass.' in resp.text

    formdef.fields[4].post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'form_var_foo == "foo"'},
            'error_message': 'You shall not pass.',
        },
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'bar'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'errornotice' in resp.text
    assert 'You shall not pass.' in resp.text

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text

    # check a post-condition raising an exception, they should always fail.
    formdef.fields[4].post_conditions = [
        {'condition': {'type': 'django', 'value': '1/0'}, 'error_message': 'You shall not pass.'},
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'bar'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'errornotice' in resp.text
    assert 'You shall not pass.' in resp.text

    # check a post-condition referring to a field on the same page
    formdef.fields[4].post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'form_var_bar == "bar"'},
            'error_message': 'You shall not pass.',
        },
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'bar'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'foo'
    resp = resp.forms[0].submit('submit')
    assert 'errornotice' in resp.text
    assert 'You shall not pass.' in resp.text

    # check a post-condition with a template as error_message
    formdef.fields[4].post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'form_var_bar == "bar"'},
            'error_message': 'You shall not {{form_var_foo}} {{form_var_bar}}.',
        },
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'bar'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'foo'
    resp = resp.forms[0].submit('submit')
    assert 'errornotice' in resp.text
    assert 'You shall not bar foo.' in resp.text

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'bar'
    resp = resp.forms[0].submit('submit')
    resp.forms[0]['f5'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text


def test_form_multi_page_conditions_and_post_conditions(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_bar == "bar"'},
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.StringField(id='1', label='string', varname='bar'),
        fields.PageField(id='3', label='2nd page'),
    ]

    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'bar'
    resp = resp.form.submit('submit')
    assert_current_page(resp, '2nd page')

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')
    assert 'You shall not pass.' in resp.text

    # add a conditional page, this will cause pages to be evaluated first
    # (and would trigger #25197)
    formdef.fields.append(
        fields.PageField(id='4', label='3rd page', condition={'type': 'django', 'value': 'True'})
    )
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'bar'
    resp = resp.form.submit('submit')
    assert_current_page(resp, '2nd page')

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')
    assert 'You shall not pass.' in resp.text


def test_form_multi_page_page_name_as_title(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.TitleField(id='4', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    page = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    page.forms[0]['f1'] = 'foo'
    next_page = page.forms[0].submit('submit')
    assert_current_page(next_page, '2nd page')
    assert next_page.forms[0]['previous']
    next_page.forms[0]['f3'] = 'bar'
    next_page = next_page.forms[0].submit('submit')
    assert_current_page(next_page, 'Validating')
    assert 'Check values then click submit.' in next_page.text
    assert next_page.text.count('1st page') == 3  # in steps (twice) and in main body

    # add a comment that will not be displayed and should therefore not be
    # considered.
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.CommentField(id='5', label='bla bla bla'),
        fields.TitleField(id='4', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    page = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    page.forms[0]['f1'] = 'foo'
    next_page = page.forms[0].submit('submit')
    assert_current_page(next_page, '2nd page')
    assert next_page.forms[0]['previous']
    next_page.forms[0]['f3'] = 'bar'
    next_page = next_page.forms[0].submit('submit')
    assert_current_page(next_page, 'Validating')
    assert 'Check values then click submit.' in next_page.text
    assert next_page.text.count('1st page') == 3  # in steps (twice) and in main body


def test_form_multi_page_go_back(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.PageField(id='3', label='3rd page'),
        fields.StringField(id='4', label='string 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert_current_page(resp, '2nd page')
    resp = resp.forms[0].submit('submit')  # -> 3rd page
    assert_current_page(resp, '3rd page')
    resp.forms[0]['f4'] = 'foo'
    resp = resp.forms[0].submit('submit')  # -> validation page
    assert_current_page(resp, 'Validating')

    # go back to second page (javascript would set this)
    resp.forms[0]['previous-page-id'] = '2'
    resp = resp.forms[0].submit('previous')
    assert_current_page(resp, '2nd page')
    resp = resp.forms[0].submit('submit')  # -> 3rd page

    # go back to first page (javascript would set this)
    resp.forms[0]['previous-page-id'] = '0'
    resp = resp.forms[0].submit('previous')
    assert_current_page(resp, '1st page')
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    resp = resp.forms[0].submit('submit')  # -> 3rd page

    # go back to invalid page (javascript would not set this)
    resp.forms[0]['previous-page-id'] = '10'
    resp = resp.forms[0].submit('previous')
    assert_current_page(resp, '1st page')  # fallback to first page


def test_form_submit_with_user(pub, emails):
    create_user(pub)
    formdef = create_formdef()
    page = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    next_page = page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert formdef.data_class().count() == 1
    assert next_page.pyquery('#summary').attr['class'] == 'section foldable folded'
    # check the user received a copy by email
    assert emails.get('New form (test)')
    assert emails.get('New form (test)')['email_rcpt'] == ['foo@localhost']


def test_form_submit_with_just_disabled_user(pub, emails):
    user = create_user(pub)
    formdef = create_formdef()
    app = login(get_app(pub), username='foo', password='foo')
    formdef.data_class().wipe()
    resp = app.get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp
    user.is_active = False
    user.store()
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Sorry, your session has been lost.' in resp


def test_form_titles(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.TitleField(id='4', label='1st page'),
        fields.SubtitleField(id='5', label='subtitle of 1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.TitleField(id='6', label='title of second page'),
        fields.StringField(id='3', label='string 2', required='optional'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert '<h3 data-field-id="0">1st page/h3>' not in resp.text
    assert '<h4 data-field-id="5">subtitle of 1st page</h4>' in resp.text
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')
    assert '<h3 data-field-id="6">title of second page</h3>' in resp.text
    resp.form['f3'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation page
    assert '<h3>1st page</h3>' in resp.text
    assert '<h4 data-field-id="5">subtitle of 1st page</h4>' in resp.text
    assert '<h3 data-field-id="6">title of second page</h3>' in resp.text
    resp = resp.form.submit('submit').follow()  # -> submit
    assert '<h3>1st page</h3>' in resp.text
    assert '<div class="title "><h3>1st page</h3></div>' not in resp.text
    assert '<div class="subtitle "><h4>subtitle of 1st page</h4></div>' in resp.text
    assert '<div class="title "><h3>title of second page</h3></div>' in resp.text


def test_form_summary_empty_pages(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='toto'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_toto == "foo"'},
        ),
        fields.TitleField(id='6', label='title in second page'),
        fields.StringField(id='3', label='string'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string'),
        fields.PageField(id='7', label='4th page'),
        fields.CommentField(id='8', label='Bla bla bla'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/test/')  # -> 1st page
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp.form['f3'] = 'bar'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp.form['f5'] = 'baz'
    resp = resp.form.submit('submit')  # -> 4th page
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')
    formdata_id = resp.location.split('/')[-2]
    resp = resp.follow()  # -> submit
    assert '<h3>1st page</h3>' in resp.text
    assert '<h3>2nd page</h3>' in resp.text
    assert '<h3>3rd page</h3>' in resp.text
    assert '<h3>4th page</h3>' not in resp.text

    resp = app.get('/test/')  # -> 1st page
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp.form['f3'] = 'bar'
    resp = resp.form.submit('previous')  # -> 1st page
    resp.form['f1'] = 'baz'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp.form['f5'] = 'baz'
    resp = resp.form.submit('submit')  # -> 4th page
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submit
    assert '<h3>1st page</h3>' in resp.text
    assert '<h3>2nd page</h3>' not in resp.text
    assert '<h3>3rd page</h3>' in resp.text
    assert '<h3>4th page</h3>' not in resp.text

    # change condition to have second page never displayed
    formdef.fields[2].condition['value'] = False
    formdef.store()
    formdata = formdef.data_class().get(formdata_id)
    resp = app.get(formdata.get_url())
    # it was filled by user, it should still appear (conditions should not be
    # replayed)
    assert '<h3>1st page</h3>' in resp.text
    assert '<h3>2nd page</h3>' in resp.text
    assert '<h3>3rd page</h3>' in resp.text
    assert '<h3>4th page</h3>' not in resp.text


def test_form_display_locations(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='1', label='string1', display_locations=[]),
        fields.StringField(id='2', label='string2', display_locations=['validation']),
        fields.StringField(id='3', label='string3', display_locations=['summary']),
        fields.CommentField(id='4', label='Bla bla bla', display_locations=['validation', 'summary']),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'plop1'
    resp.form['f2'] = 'plop2'
    resp.form['f3'] = 'plop3'
    resp = resp.form.submit('submit')  # -> validation
    pq = resp.pyquery.remove_namespaces()
    assert pq('div[style="display: none;"] [name=f1]')
    assert not pq('div[style="display: none;"] [name=f2]')
    assert pq('div[style="display: none;"] [name=f3]')
    assert 'Bla bla bla' in resp.text

    resp = resp.form.submit('submit').follow()  # -> submit
    assert formdef.data_class().select()[0].data['1'] == 'plop1'
    assert formdef.data_class().select()[0].data['2'] == 'plop2'
    assert formdef.data_class().select()[0].data['3'] == 'plop3'
    assert 'plop1' not in resp.text
    assert 'plop2' not in resp.text
    assert 'plop3' in resp.text
    assert 'Bla bla bla' in resp.text


def test_multipage_form_display_locations(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string1', display_locations=[]),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(id='3', label='Bla bla bla', display_locations=['validation']),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'plop1'
    resp = resp.form.submit('submit')  # -> page 2
    resp = resp.form.submit('submit')  # -> validation

    pq = resp.pyquery.remove_namespaces()
    assert '<h3>1st page</h3>' not in resp.text  # page 1 title not displayed
    assert pq('div[style="display: none;"] [name=f1]')  # but page 1 field included, hidden
    assert '<h3>2nd page</h3>' in resp.text  # page 2 title
    assert 'Bla bla bla' in resp.text  # and page 2 comment field


def test_form_visit_existing(pub):
    user = create_user(pub)
    formdef = create_formdef()
    login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.store()

    formdata_user = formdef.data_class()()
    formdata_user.user_id = user.id
    formdata_user.store()

    resp = get_app(pub).get('/test/%s/' % formdata.id)
    assert resp.location.startswith('http://example.net/login/?next=')

    resp = get_app(pub).get('/test/%s/' % formdata_user.id)
    assert resp.location.startswith('http://example.net/login/?next=')

    resp = login(get_app(pub), username='foo', password='foo').get('/test/%s/' % formdata_user.id)
    assert 'The form has been recorded on' in resp


def form_password_field_submit(app, password):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [fields.PasswordField(id='0', label='password', formats=['sha1', 'md5', 'cleartext'])]
    formdef.store()
    page = app.get('/test/')
    formdef.data_class().wipe()
    next_page = page.forms[0].submit('submit')  # but the field is required
    assert [x.text for x in next_page.pyquery('div.error p')] == ['required field'] * 2
    next_page.forms[0]['f0$pwd1'] = password
    next_page.forms[0]['f0$pwd2'] = password
    next_page = next_page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {
        '0': {
            'sha1': force_str(hashlib.sha1(force_bytes(password)).hexdigest()),
            'md5': force_str(hashlib.md5(force_bytes(password)).hexdigest()),
            'cleartext': force_str(password),
        }
    }


def test_form_password_field_submit(pub):
    create_user(pub)
    form_password_field_submit(get_app(pub), 'foobar')
    form_password_field_submit(get_app(pub), force_str('•	83003706'))
    form_password_field_submit(login(get_app(pub), username='foo', password='foo'), 'foobar\u00eb')


def test_form_multi_page_formdef_count_condition(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_objects.count > 0'},
        ),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text

    # add a formdata this will make the second page appear.
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # should NOT go straight to validation
    assert 'Check values then click submit.' not in resp.text


def test_form_multi_page_post_edit(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    page = login(get_app(pub), username='foo', password='foo').get('/test/')
    page.forms[0]['f1'] = 'foo'
    next_page = page.forms[0].submit('submit')
    next_page.forms[0]['f3'] = 'barXYZ'
    next_page = next_page.forms[0].submit('submit')
    next_page = next_page.forms[0].submit('submit')
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text

    data_id = formdef.data_class().select()[0].id

    page = login(get_app(pub), username='foo', password='foo').get('/test/%s/' % data_id)
    assert 'button_editable-button' in page.text
    assert 'barXYZ' in page.text

    resp = page.forms[0].submit('button_editable')
    assert resp.location.startswith('http://example.net/test/%s/wfedit-' % data_id)
    resp = resp.follow()
    # check there's no new "phantom" history entry
    assert len(formdef.data_class().get(data_id).evolution) == 1
    assert resp.forms[0]['f1'].value == 'foo'
    resp.forms[0]['f1'] = 'foo2'

    resp = resp.forms[0].submit('submit')
    assert resp.forms[0]['f3'].value == 'barXYZ'
    resp = resp.forms[0].submit('previous')
    assert resp.forms[0]['f1'].value == 'foo2'
    resp = resp.forms[0].submit('submit')
    assert 'Save Changes' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % data_id
    resp = resp.follow()
    assert 'foo2' in resp.text  # modified value is there
    assert 'barXYZ' in resp.text  # unchanged value is still there
    assert len(formdef.data_class().get(data_id).evolution) == 2  # new history entry
    assert formdef.data_class().get(data_id).evolution[-1].who == '_submitter'
    assert formdef.data_class().get(data_id).evolution[-1].status is None

    # modify workflow to jump to another status after the edition
    st2 = workflow.add_status('Status2', 'st2')
    editable.status = st2.id
    workflow.store()

    assert formdef.data_class().get(data_id).status == 'wf-%s' % st1.id
    page = login(get_app(pub), username='foo', password='foo').get('/test/%s/' % data_id)
    assert 'button_editable-button' in page.text
    assert 'barXYZ' in page.text

    resp = page.forms[0].submit('button_editable')
    assert resp.location.startswith('http://example.net/test/%s/wfedit-' % data_id)
    resp = resp.follow()
    assert resp.forms[0]['f1'].value == 'foo2'
    resp.forms[0]['f1'] = 'foo3'
    resp = resp.forms[0].submit('submit')
    assert (
        formdef.data_class().get(data_id).data['1'] == 'foo2'
    )  # check foo3 has not been overwritten in database
    assert resp.forms[0]['f3'].value == 'barXYZ'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % data_id
    resp = resp.follow()
    assert 'foo3' in resp.text  # modified value is there
    assert 'barXYZ' in resp.text  # unchanged value is still there
    assert formdef.data_class().get(data_id).status == 'wf-%s' % st2.id
    assert len(formdef.data_class().get(data_id).evolution) == 3  # single new history entry
    assert formdef.data_class().get(data_id).evolution[-1].who == '_submitter'
    assert formdef.data_class().get(data_id).evolution[-1].status == 'wf-%s' % st2.id

    # jump to a nonexistent status == do not jump, but add a LoggedError
    LoggedError.wipe()
    assert LoggedError.count() == 0
    editable.status = 'deleted_status_id'
    workflow.store()
    # go back to st1
    formdata = formdef.data_class().get(data_id)
    formdata.status = 'wf-%s' % st1.id
    formdata.store()
    assert formdef.data_class().get(data_id).status == 'wf-%s' % st1.id
    page = login(get_app(pub), username='foo', password='foo').get('/test/%s/' % data_id)
    resp = page.forms[0].submit('button_editable')
    resp = resp.follow()
    resp.forms[0]['f1'] = 'foo3'
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert formdef.data_class().get(data_id).status == 'wf-%s' % st1.id  # stay on st1
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdata_id == str(formdata.id)
    assert logged_error.formdef_id == str(formdef.id)
    assert logged_error.workflow_id == str(workflow.id)
    assert logged_error.status_id == st1.id
    assert logged_error.status_item_id == editable.id
    assert logged_error.occurences_count == 1

    # do it again: increment logged_error.occurences_count
    page = login(get_app(pub), username='foo', password='foo').get('/test/%s/' % data_id)
    resp = page.forms[0].submit('button_editable')
    resp = resp.follow()
    resp.forms[0]['f1'] = 'foo3'
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert formdef.data_class().get(data_id).status == 'wf-%s' % st1.id  # stay on st1
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.occurences_count == 2


def test_form_edit_autocomplete_list(pub):
    create_user(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    formdef = create_formdef()
    formdef.data_class().wipe()

    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
        ),
    ]
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get('http://remote.example.net/json', json=data)

        resp = app.get('/test/')
        assert 'data-select2-url=' in resp.text
        # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
        resp.form.fields['f0_display'] = Hidden(form=resp.form, tag='input', name='f0_display', pos=10)
        resp.form['f0'].force_value('1')
        resp.form.fields['f0_display'].force_value('hello')
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '1'
        assert formdef.data_class().select()[0].data['0_display'] == 'hello'
        assert formdef.data_class().select()[0].data['0_structured'] == data['data'][0]

        resp = resp.follow()
        url = resp.request.url
        resp = resp.form.submit('button_editable')
        assert 'wfedit' in resp.location
        resp = resp.follow()
        assert 'data-value="1"' in resp
        assert 'data-initial-display-value="hello"' in resp

        # relogin
        app = get_app(pub)
        login(app, username='foo', password='foo')
        resp = app.get(url)
        resp = resp.form.submit('button_editable')
        resp = resp.follow()
        assert 'data-value="1"' in resp
        assert 'data-initial-display-value="hello"' in resp


def test_form_edit_with_internal_id_condition(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()

    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
        fields.StringField(
            id='2',
            label='2nd field',
            condition={'type': 'django', 'value': 'form_internal_id'},
        ),
    ]
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')

    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    assert 'f2' not in resp.form.fields
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1'] == 'test'

    resp = resp.follow()
    resp = resp.form.submit('button_editable')
    assert 'wfedit' in resp.location
    resp = resp.follow()
    assert 'f2' in resp.form.fields


def test_form_edit_action_jump_to_previously_marked(pub):
    create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1')
    st2 = workflow.add_status('Status2')

    choice = st1.add_action('choice')
    choice.label = 'go to status2'
    choice.by = ['_submitter']
    choice.status = st2.id
    choice.set_marker_on_status = True

    editable = st2.add_action('editable')
    editable.by = ['_submitter']
    editable.label = 'edit'
    editable.status = '_previous'

    workflow.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    resp = resp.forms[0].submit('button1').follow()  # jump
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st2.id}'

    resp = resp.forms[0].submit('button1')  # edit
    resp = resp.follow()
    resp.forms[0]['f1'] = 'foo2'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st1.id}'


def test_form_count_dispatching(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.condition = {'type': 'django', 'value': 'form_objects.count_status_st2 < 1'}
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    page = login(get_app(pub), username='foo', password='foo').get('/test/')
    page = page.forms[0].submit('submit')  # form page
    page = page.forms[0].submit('submit')  # confirmation page
    page = page.follow()
    assert 'The form has been recorded' in page.text  # success

    assert len(formdef.data_class().select(clause=lambda x: x.status == 'wf-st1')) == 0
    assert len(formdef.data_class().select(clause=lambda x: x.status == 'wf-st2')) == 1

    page = login(get_app(pub), username='foo', password='foo').get('/test/')
    page = page.forms[0].submit('submit')  # form page
    page = page.forms[0].submit('submit')  # confirmation page
    page = page.follow()
    assert 'The form has been recorded' in page.text  # success

    assert len(formdef.data_class().select(clause=lambda x: x.status == 'wf-st2')) == 1
    assert len(formdef.data_class().select(clause=lambda x: x.status == 'wf-st1')) == 1


def test_preview_form(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = []
    formdef.disabled = True
    formdef.store()

    # check the preview page is not accessible to regular users
    get_app(pub).get('/preview/test/', status=403)

    # check it's accessible to admins
    user.is_admin = True
    user.store()
    page = login(get_app(pub), username='foo', password='foo').get('/preview/test/')

    # check the form is marked as a preview (this disables autosave calls)
    assert page.pyquery('form[data-autosave=false]').length

    # check no formdata gets stored
    next_page = page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 200
    assert 'This was only a preview: form was not actually submitted.' in next_page.text
    assert len([x for x in formdef.data_class().select() if not x.is_draft()]) == 0

    # check no drafts are proposed for recall
    formdef.data_class().wipe()
    draft = formdef.data_class()()
    draft.user_id = user.id
    draft.status = 'draft'
    draft.data = {}
    draft.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/preview/test/')
    assert 'You already started to fill this form.' not in resp.text

    # check the preview is ok when there is a category
    Category.wipe()
    cat = Category(name='foobar')
    cat.store()
    formdef.category_id = cat.id
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/preview/test/', status=200)


def test_form_captcha(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='Some field')]
    formdef.has_captcha = True
    formdef.enable_tracking_codes = True
    formdef.store()

    # test authenticated users are not presented with a captcha
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp = resp.click('test')
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'form_captcha' not in resp.text

    # check anonymous user gets the captcha
    app = get_app(pub)
    resp = app.get('/')
    resp = resp.click('test')
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'form_captcha' in resp.text

    session_id = list(app.cookies.values())[0].strip('"')
    session = pub.session_class.get(session_id)
    resp.form['captcha$q'] = session.get_captcha_token(resp.forms[0]['captcha$token'].value)['answer']
    resp = resp.form.submit('submit')
    assert resp.status_code == 302  # redirect when formdata is created

    # and check it gets it only once
    resp = app.get('/')
    resp = resp.click('test')
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'form_captcha' not in resp.text


def test_form_captcha_and_no_validation_page(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='Some field')]
    formdef.has_captcha = True
    formdef.enable_tracking_codes = True
    formdef.confirmation = False
    formdef.store()

    # test authenticated users are not stopped on a confirmation page
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp = resp.click('test')
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')
    assert resp.status_code == 302  # redirect when formdata is created

    # check anonymous user gets the captcha
    app = get_app(pub)
    resp = app.get('/')
    resp = resp.click('test')
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'form_captcha' in resp.text


def test_form_table_field_submit(pub, emails):
    formdef = create_formdef()
    formdef.fields = [
        fields.TableField(
            id='0',
            label='table',
            rows=[force_str('à'), 'b'],
            columns=['c', 'd', force_str('e')],
            required='optional',
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().select()[0].data == {'0': [['', '', ''], ['', '', '']]}
    formdef.data_class().wipe()

    formdef.fields = [
        fields.TableField(
            id='0', label='table', rows=['a', 'b'], columns=['c', 'd', 'e'], required='required'
        )
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' not in resp.text

    resp = get_app(pub).get('/test/')
    resp.form['f0$c-0-0'] = 'a'
    resp.form['f0$c-1-0'] = 'b'
    resp.form['f0$c-0-1'] = 'c'
    resp.form['f0$c-1-1'] = 'd'
    resp.form['f0$c-0-2'] = 'e'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().select()[0].data == {'0': [['a', 'c', 'e'], ['b', 'd', '']]}

    # check table is present in received email (via form_details).
    create_user(pub)
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0$c-0-0'] = 'àà'  # would trigger column length bug (#23072)
    resp.form['f0$c-1-0'] = 'bb'
    resp.form['f0$c-0-1'] = 'cc'
    resp.form['f0$c-1-1'] = 'dd'
    resp.form['f0$c-0-2'] = 'ee'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    # check rst2html didn't fail
    assert b'ee' in emails.get('New form (test)')['msg'].get_payload()[1].get_payload(decode=True)


def test_form_table_rows_field_submit(pub, emails):
    formdef = create_formdef()
    formdef.fields = [fields.TableRowsField(id='0', label='table', columns=['a', 'b'], required='optional')]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().select()[0].data == {'0': []}
    formdef.data_class().wipe()

    formdef.fields = [fields.TableRowsField(id='0', label='table', columns=['a', 'b'], required='required')]
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' not in resp.text

    resp = get_app(pub).get('/test/')
    resp.form['f0$element0$col0'] = 'a'
    resp.form['f0$element0$col1'] = 'b'
    resp.form['f0$element1$col0'] = 'c'
    resp.form['f0$element1$col1'] = 'd'
    resp.form['f0$element2$col0'] = 'e'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().select()[0].data == {'0': [['a', 'b'], ['c', 'd'], ['e', '']]}

    formdef.data_class().wipe()

    formdef.fields = [
        fields.TableRowsField(id='0', label='table', columns=['a', 'b'], required='required', total_row=True)
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f0$element0$col0'] = 'a'
    resp.form['f0$element0$col1'] = '14'
    resp.form['f0$element1$col0'] = 'c'
    resp.form['f0$element1$col1'] = '23'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert '37.00' in resp.text

    # check table is present in received email (via form_details).
    create_user(pub)
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0$element0$col0'] = 'àà'
    resp.form['f0$element0$col1'] = '14'
    resp.form['f0$element1$col0'] = 'ee'
    resp.form['f0$element1$col1'] = '23'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert b'ee' in emails.get('New form (test)')['msg'].get_payload()[1].get_payload(decode=True)


def test_form_table_rows_add_row(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='1', label='string', require=True),
        fields.TableRowsField(id='0', label='table', columns=['a', 'b'], required='required'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert len(resp.pyquery.find('input[name^="f0$element"]')) == 10
    resp = resp.form.submit('f0$add_element')
    assert 'There were errors processing the form' not in resp
    assert len(resp.pyquery.find('input[name^="f0$element"]')) == 12
    resp = resp.form.submit('f0$add_element')
    assert len(resp.pyquery.find('input[name^="f0$element"]')) == 14
    resp = resp.form.submit('submit')
    assert 'There were errors processing the form' in resp


def test_form_middle_session_change(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f1'] = 'foo'
    assert resp.forms[0].fields['submit'][0].value_if_submitted() == 'Next'
    resp = resp.forms[0].submit('submit')
    assert resp.forms[0]['previous']
    app.cookiejar.clear()
    resp.forms[0]['f3'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/'
    resp = resp.follow()
    assert 'Sorry, your session has been lost.' in resp.text

    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f1'] = 'foo'
    assert resp.forms[0].fields['submit'][0].value_if_submitted() == 'Next'
    resp = resp.forms[0].submit('submit')
    assert resp.forms[0]['previous']
    resp.forms[0]['f3'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    app.cookiejar.clear()
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert 'Sorry, your session has been lost.' in resp.text


def test_form_autocomplete_variadic_url(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='3', label='2nd page', condition={'type': 'django', 'value': 'True'}),
        fields.ItemField(id='1', label='string', varname='foo', items=['Foo', 'Bar']),
        fields.StringField(
            id='2',
            label='string2',
            required='required',
            data_source={'type': 'jsonp', 'value': '[var_foo]'},
        ),
        fields.PageField(id='4', label='3rd page', condition={'type': 'django', 'value': 'True'}),
    ]
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')  # next
    # test javascript will be used to compute the full URL
    assert 'options.wcs_base_url' in resp.text
    assert 'jquery-ui.min.js' in resp.text

    # test going forward (will error out), check it's still a variadic URL (#9786)
    resp.form['f1'] = 'Foo'
    resp = resp.form.submit('submit')
    assert 'options.wcs_base_url' in resp.text


def test_form_date_field_submit(pub):
    formdef = create_formdef()
    formdef.fields = [fields.DateField(id='0', label='string', required='optional')]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f0'] = '2015-01-01'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert time.strftime('%Y-%m-%d', data.data['0']) == '2015-01-01'

    # without filling the field
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] is None


def test_form_string_regex_field_submit(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(
            id='0',
            label='string',
            validation={'type': 'regex', 'value': r'\d{5}$'},
            required='optional',
        )
    ]
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = '12345'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == '12345'

    # without filling the field
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] is None

    # with an invalid input
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert 'invalid value' in resp.text


def test_form_text_field_submit(pub):
    formdef = create_formdef()
    formdef.fields = [fields.TextField(id='0', label='string', required='optional')]
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = '12345'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == '12345'

    # without filling the field
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] is None

    # check max length
    formdef.fields = [fields.TextField(id='0', label='string', maxlength=10)]
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'x' * 11
    resp = resp.forms[0].submit('submit')
    assert 'too many characters (limit is 10)' in resp.text
    # check it counts characters, not bytes
    resp.forms[0]['f0'] = '☭' * 10
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text


def test_unknown_datasource(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(
            id='1', label='string', varname='string', required='optional', data_source={'type': 'foobar'}
        ),
        fields.ItemField(
            id='2', label='item', varname='item', required='optional', data_source={'type': 'foobar'}
        ),
        fields.ItemsField(
            id='3', label='items', varname='items', required='optional', data_source={'type': 'foobar'}
        ),
    ]

    formdef.store()
    data_class = formdef.data_class()
    data_class.wipe()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text


def test_form_ranked_items_field_submit(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.RankedItemsField(
            id='0', label='ranked items', required='optional', items=['foo', 'bar', 'baz']
        )
    ]
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.form['f0$element0'] = '1'
    resp.form['f0$element1'] = '2'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == {'bar': 2, 'foo': 1}


def test_form_ranked_items_randomize_order(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.RankedItemsField(
            id='0',
            label='ranked items',
            required='optional',
            randomize_items=True,
            items=['foo', 'bar', 'baz'],
        )
    ]
    formdef.store()
    orders = {}
    for _ in range(10):
        resp = get_app(pub).get('/test/')
        orders['%s-%s-%s' % (resp.text.index('foo'), resp.text.index('bar'), resp.text.index('baz'))] = True
    assert len(orders.keys()) > 1


def test_form_autosave(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'

    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdef.data_class().select()[0].data['1'] == 'foobar'

    resp.form['f1'] = 'foobar2'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().select()[0].data['1'] == 'foobar2'

    resp.form['f1'] = 'foobar3'
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'

    resp.form['f3'] = 'xxx'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'
    assert formdef.data_class().select()[0].data['3'] == 'xxx'

    resp.form['f3'] = 'xxx2'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'
    assert formdef.data_class().select()[0].data['3'] == 'xxx2'

    resp.form['f3'] = 'xxx3'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'foobar3' in resp.text
    assert 'xxx3' in resp.text

    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'
    assert formdef.data_class().select()[0].data['3'] == 'xxx3'

    # make sure autosave() doesn't destroy data that would have been submitted
    # in the meantime
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'
    autosave_fields = resp.form.submit_fields()
    resp.form['f1'] = 'foobar3'
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'

    # post content with 'foobar' as value, it should not be saved
    ajax_resp = app.post('/test/autosave', params=autosave_fields)
    assert json.loads(ajax_resp.text)['result'] == 'error'
    assert formdef.data_class().select()[0].data['1'] == 'foobar3'


def test_form_autosave_timeout(pub, monkeypatch):
    from wcs.forms.root import FormPage

    monkeypatch.setattr(FormPage, 'AUTOSAVE_TIMEOUT', 0.0001)

    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'

    resp = app.post('/test/autosave', params=resp.form.submit_fields())
    assert resp.json == {'reason': 'too long', 'result': 'error'}


def test_form_autosave_with_invalid_data(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.EmailField(id='1', label='email'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'  # not a valid email

    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdef.data_class().select()[0].data['1'] == 'foobar'

    # restore draft
    tracking_code = get_displayed_tracking_code(resp)
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit().follow().follow().follow()
    assert resp.forms[1]['f1'].value == 'foobar'  # not a valid email


def test_form_autosave_with_parameterized_datasource(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemField(
            id='3',
            label='item',
            data_source={'type': 'jsonvalue', 'value': '''[{"id": "1", "text": "X{{ form_var_foo }}"}]'''},
        ),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'bar'

    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdef.data_class().select()[0].data['1'] == 'bar'
    assert formdef.data_class().select()[0].data.get('3') is None

    resp = resp.forms[0].submit('submit')
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].data['1'] == 'bar'
    assert formdef.data_class().select()[0].data['3'] == '1'
    assert formdef.data_class().select()[0].data['3_display'] == 'Xbar'


def test_form_autosave_never_overwrite(pub, settings):
    create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()

    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string2'),
    ]
    formdef.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')

    resp = app.get('/test/')
    resp.form['f1'] = '1'
    # go to the second page
    resp = resp.form.submit('submit')
    resp.form['f3'] = 'tmp'
    # autosave this temporary data
    autosave_data = dict(resp.form.submit_fields())
    resp_autosave = app.post('/test/autosave', params=autosave_data)
    assert resp_autosave.json == {'result': 'success'}
    # check the draft has been modified
    formdata = formdef.data_class().select()[0]
    formdata.refresh_from_storage()
    assert formdata.data['3'] == 'tmp'
    # now finish submitting with new value
    resp.form['f3'] = '1'
    resp = resp.form.submit('submit')  # -> validation page
    formdata.refresh_from_storage()
    assert formdata.data['3'] == '1'
    # autosave wrong data
    # _ajax_form_token is just a form_token, so take the current one to
    # simulate a rogue autosave from the previous page
    autosave_data['_ajax_form_token'] = resp.form['_form_id'].value
    resp_autosave = app.post('/test/autosave', params=autosave_data)
    formdata.refresh_from_storage()
    assert resp_autosave.json != {'result': 'success'}
    assert formdata.data['3'] == '1'
    # validate
    resp = resp.form.submit('submit')  # -> submit

    # everything is still fine in the end, even for pickle storage
    # (as the overwritten # data are recreated from validation page)
    assert formdef.data_class().select()[0].data == {'1': '1', '3': '1'}


def test_form_string_field_autocomplete(pub):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string', required='optional')]
    formdef.fields[0].data_source = {'type': 'jsonp'}
    formdef.store()

    # not filled completed, no call to .autocomplete
    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' not in resp.text

    # straight URL
    formdef.fields[0].data_source = {'type': 'jsonp', 'value': 'http://example.org'}
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' in resp.text
    assert 'http://example.org' in resp.text

    # URL from variable
    formdef.fields[0].data_source = {'type': 'jsonp', 'value': '[site_url]'}
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' in resp.text
    assert 'http://example.net' in resp.text


def test_form_string_field_autocomplete_named_datasource(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='string', required='optional', data_source={'type': 'foobar'})
    ]
    formdef.store()

    # jsonp datasource
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonp', 'value': 'http://remote.example.net/json'}
    data_source.store()

    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' in resp.text
    assert "options.url = 'http://remote.example.net/json'" in resp.text
    assert "options.url = '/api/autocomplete/" not in resp.text
    assert 'dataType: "jsonp",' in resp.text

    # json datasource
    data_source.data_source['type'] = 'json'
    data_source.query_parameter = 'q'
    data_source.store()

    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' in resp.text
    assert "options.url = 'http://remote.example.net/json'" not in resp.text
    assert "options.url = '/api/autocomplete/" in resp.text
    assert 'dataType: "json",' in resp.text

    # card datasource
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Foo'
    carddef.fields = []
    carddef.store()

    data_source.data_source['type'] = 'carddef:foo'
    data_source.store()
    resp = get_app(pub).get('/test/')
    assert ').autocomplete({' in resp.text
    assert "options.url = 'http://remote.example.net/json'" not in resp.text
    assert "options.url = '/api/autocomplete/" in resp.text
    assert 'dataType: "json",' in resp.text


def test_form_autocomplete_named_datasource_expired_token(pub):
    CardDef.wipe()
    FormDef.wipe()
    TransientData.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='string', required='optional', data_source={'type': 'foobar'})
    ]
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Foo'
    carddef.fields = []
    carddef.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'carddef:foo'}
    data_source.store()

    resp = get_app(pub).get('/test/')
    assert TransientData.count() == 1
    token = TransientData.select()[0]
    assert '/api/autocomplete/%s' % token.id in resp.text

    # new session, check a new token is generated
    resp = get_app(pub).get('/test/')
    assert '/api/autocomplete/%s' % token.id not in resp.text


@pytest.mark.parametrize('sign', ['without-signature', 'with-signature'])
def test_form_autocomplete_named_datasource_cache_duration(pub, sign):
    CardDef.wipe()
    FormDef.wipe()
    TransientData.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='string', required='optional', data_source={'type': 'foobar'})
    ]
    formdef.store()

    url = 'http://remote.example.net/json_%s_%s' % (hashlib.sha1(pub.app_dir.encode()).hexdigest(), sign)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': url}
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.cache_duration = '1200'
    data_source.store()

    if sign == 'with-signature':
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            fd.write(
                '''\
[wscall-secrets]
remote.example.net = 1234
    '''
            )

    app = get_app(pub)
    resp = app.get('/test/')
    assert TransientData.count() == 1
    token = TransientData.select()[0]
    assert '/api/autocomplete/%s' % token.id in resp.text

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get(url, json=data)

        resp = app.get('/api/autocomplete/%s?q=a' % token.id)
        assert len(rsps.calls) == 1
        assert len(resp.json['data']) == 2
        resp = app.get('/api/autocomplete/%s?q=a' % token.id)
        assert len(rsps.calls) == 1  # cached
        assert len(resp.json['data']) == 2

        resp = app.get('/api/autocomplete/%s?q=b' % token.id)
        assert len(rsps.calls) == 2  # not cached
        assert len(resp.json['data']) == 2


def test_form_workflow_trigger(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'

    jump2 = st1.add_action('jump')
    jump2.trigger = 'YYY'
    jump2.mode = 'trigger'
    jump2.status = 'st3'
    jump2.set_marker_on_status = True

    st2 = workflow.add_status('Status2', 'st2')
    workflow.add_status('Status3', 'st3')
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    app = get_app(pub)
    login(app, username='foo', password='foo').get('/')
    app.post(formdata.get_url() + 'jump/trigger/XXX', status=403)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    jump.by = [role.id]
    workflow.store()
    app.post(formdata.get_url() + 'jump/trigger/XXX', status=403)

    user.roles = [role.id]
    user.store()
    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)
    assert resp.location == formdata.get_url()
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.status == 'wf-st2'

    formdata.status = 'wf-st1'
    formdata.store()
    app.post(formdata.get_url() + 'jump/trigger/YYY', status=403)
    jump2.by = [role.id]
    workflow.store()
    app.post(formdata.get_url() + 'jump/trigger/YYY', status=302)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.status == 'wf-st3'
    assert formdata.workflow_data.get('_markers_stack') == [{'status_id': 'st1'}]

    formdata.status = 'wf-st1'
    formdata.store()
    app.post(
        formdata.get_url() + 'jump/trigger/YYY',
        params=json.dumps({'data': {'foo': 'bar'}}),
        content_type='application/json',
    )
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.workflow_data.get('data') == {'foo': 'bar'}

    # check with redirect action
    formdata.status = 'wf-st1'
    formdata.store()
    redirect = st2.add_action('redirect_to_url')
    redirect.url = 'https://example.net'
    workflow.store()

    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)
    assert resp.location == 'https://example.net'


def test_form_worklow_multiple_identical_status(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    st1.extra_css_class = 'CSS-STATUS1'
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st1'
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    app = get_app(pub)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.allows_backoffice_access = False
    role.store()

    jump.by = [role.id]
    workflow.store()
    user.roles = [role.id]
    user.store()

    assert len(formdef.data_class().get(formdata.id).evolution) == 1
    assert formdef.data_class().get(formdata.id).evolution[0].last_jump_datetime is None

    login(app, username='foo', password='foo')
    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)
    formdata = formdef.data_class().get(formdata.id)
    # status is not changed: no new evolution, only a new last_jump_datetime
    assert len(formdata.evolution) == 1
    assert formdata.status == 'wf-st1'
    assert formdata.evolution[0].last_jump_datetime is not None
    assert (
        formdef.data_class().get(formdata.id).get_static_substitution_variables()['form_status_changed']
        is False
    )
    assert formdef.data_class().get(formdata.id).get_substitution_variables()['form_status_changed'] is False

    # add a comment to last evolution, forcing create a new one
    formdata.evolution[-1].comment = 'new-evolution-1'
    formdata.store()
    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)
    formdata = formdef.data_class().get(formdata.id)
    assert len(formdata.evolution) == 2
    assert formdata.status == 'wf-st1'
    assert (
        formdef.data_class().get(formdata.id).get_static_substitution_variables()['form_status_changed']
        is False
    )
    assert formdef.data_class().get(formdata.id).get_substitution_variables()['form_status_changed'] is False

    # again
    formdata.evolution[-1].comment = 'new-evolution-2'
    formdata.store()
    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)

    # last evolution is empty, this last trigger does not create a new one
    resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)

    # finally, 3 evolutions: new-evolution-1, new-evolution-2, empty
    formdata = formdef.data_class().get(formdata.id)
    assert len(formdata.evolution) == 3
    assert formdata.status == 'wf-st1'
    assert formdata.evolution[0].comment == 'new-evolution-1'
    assert formdata.evolution[1].comment == 'new-evolution-2'
    assert formdata.evolution[2].comment is None

    # mark user as owner so it can check the UI
    formdata.user_id = user.id
    formdata.store()
    resp = app.get(formdata.get_url())
    assert resp.text.count('Status1') == 2  # two in journal
    assert resp.text.count('CSS-STATUS1') == 2
    assert resp.text.count('new-evolution-1') == 1
    assert resp.text.count('new-evolution-2') == 1


def test_form_worklow_comments_on_same_status(pub):
    pub.session_manager.session_class.wipe()
    user = create_user(pub)

    role = pub.role_class(name='xxx')
    role.allows_backoffice_access = True
    role.store()
    user.roles = [role.id]
    user.store()

    formdef = create_formdef()
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    workflow = Workflow.get_default_workflow()
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-new'

    app = get_app(pub)

    assert (
        formdef.data_class().get(formdata.id).get_static_substitution_variables()['form_status_changed']
        is True
    )
    assert formdef.data_class().get(formdata.id).get_substitution_variables()['form_status_changed'] is True
    login(app, username='foo', password='foo')
    resp = app.get(formdata.get_url()).follow()
    resp.form['comment'] = 'TEST COMMENT'
    resp = resp.form.submit('button_commentable')
    assert (
        formdef.data_class().get(formdata.id).get_static_substitution_variables()['form_status_changed']
        is False
    )
    assert formdef.data_class().get(formdata.id).get_substitution_variables()['form_status_changed'] is False

    resp = app.get(formdata.get_url()).follow()
    resp = resp.form.submit('button_accept')
    assert (
        formdef.data_class().get(formdata.id).get_static_substitution_variables()['form_status_changed']
        is True
    )
    assert formdef.data_class().get(formdata.id).get_substitution_variables()['form_status_changed'] is True


def test_form_worklow_double_comments(pub):
    Workflow.wipe()

    create_user(pub)

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='1')
    commentable.by = [logged_users_role().id]
    commentable = st1.add_action('commentable', id='2')
    commentable.by = [logged_users_role().id]
    wf.store()

    formdef = create_formdef()
    formdef.workflow = wf
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.perform_workflow()
    formdata.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdata.get_url())
    resp.form['comment'] = 'TEST COMMENT'
    resp = resp.form.submit('button_commentable').follow()
    assert resp.text.count('TEST COMMENT') == 1


def test_display_message(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    jump = st0.add_action('jump')
    jump.status = 'st1'

    st1 = workflow.add_status('Status1', 'st1')

    display1 = st1.add_action('displaymsg')
    display1.message = 'message-to-all'
    display1.to = []

    display2 = st1.add_action('displaymsg')
    display2.message = 'message-to-submitter'
    display2.to = ['_submitter']

    display3 = st1.add_action('displaymsg')
    display3.message = 'message-to-nobody'
    display3.to = ['xxx']

    display4 = st1.add_action('displaymsg')
    display4.message = 'message-to-xxx-and-submitter'
    display4.to = ['_submitter', 'xxx']

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    page = app.get('/test/')
    page = page.forms[0].submit('submit')  # form page
    page = page.forms[0].submit('submit')  # confirmation page
    page = page.follow()

    assert 'message-to-all' in page.text
    assert 'message-to-submitter' in page.text
    assert 'message-to-nobody' not in page.text
    assert 'message-to-xxx-and-submitter' in page.text
    assert page.text.index('message-to-submitter') < page.text.index('message-to-xxx-and-submitter')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]

    # actions alert vs top alert
    display2.position = 'actions'
    workflow.store()

    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text
    assert 'message-to-submitter' not in page.text
    assert 'message-to-xxx-and-submitter' in page.text

    # add an action, so display2 will appear again
    jump1 = st1.add_action('choice', id='_jump1')
    jump1.label = 'Jump 1'
    jump1.by = ['_submitter']
    jump1.status = st1.id
    workflow.store()

    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text
    assert 'message-to-submitter' in page.text
    assert 'message-to-xxx-and-submitter' in page.text
    assert page.text.index('message-to-submitter') > page.text.index('message-to-xxx-and-submitter')

    jump1.by = ['xxx']
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text
    assert 'message-to-submitter' not in page.text
    assert 'message-to-xxx-and-submitter' in page.text

    # change to always display at the bottom
    display2.position = 'bottom'
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text
    assert 'message-to-submitter' in page.text
    assert 'message-to-xxx-and-submitter' in page.text
    assert page.text.index('message-to-submitter') > page.text.index('message-to-xxx-and-submitter')

    # set a level
    display2.level = 'warning'
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'warningnotice' in page.text

    # check message is not displayed if status is not visible to user
    st1.visibility = ['_receiver']
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'warningnotice' not in page.text


def test_workflow_condition_on_message(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    display1 = st1.add_action('displaymsg')
    display1.message = 'message-to-all'
    display1.to = []

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    page = app.get('/test/')
    page = page.forms[0].submit('submit')  # form page
    page = page.forms[0].submit('submit')  # confirmation page
    page = page.follow()
    assert 'message-to-all' in page.text

    formdata = formdef.data_class().select()[0]
    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text

    display1.condition = {'type': 'django', 'value': 'xxx'}
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'message-to-all' not in page.text


def test_workflow_message_with_template_error(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    display1 = st1.add_action('displaymsg')
    display1.message = '<p>{% for x in 0 %}crash{% endfor %}'
    display1.to = []

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')  # form page
    resp = resp.forms[0].submit('submit')  # confirmation page
    resp = resp.follow()
    assert 'Error rendering message.' in resp.text

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == "Error in template of workflow message ('int' object is not iterable)"


def test_workflow_condition_on_message_age_in_hours(pub, freezer):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    display1 = st1.add_action('displaymsg')
    display1.message = 'message-to-all'
    display1.to = []

    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    page = app.get('/test/')
    page = page.forms[0].submit('submit')  # form page
    page = page.forms[0].submit('submit')  # confirmation page
    page = page.follow()
    assert 'message-to-all' in page.text

    formdata = formdef.data_class().select()[0]
    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text

    display1.condition = {'type': 'django', 'value': 'form_receipt_datetime|age_in_hours >= 1'}
    workflow.store()
    page = app.get(formdata.get_url())
    assert 'message-to-all' not in page.text

    freezer.tick(60 * 60)
    page = app.get(formdata.get_url())
    assert 'message-to-all' in page.text


def test_session_cookie_flags(pub):
    create_formdef()
    app = get_app(pub)
    resp = app.get('/test/', status=200)
    assert resp.headers['Set-Cookie'].strip().startswith('sessionid-')
    assert 'HttpOnly' in resp.headers['Set-Cookie']
    assert 'Secure' not in resp.headers['Set-Cookie']

    app = get_app(pub, https=True)
    resp = app.get('/test/', status=200)
    assert resp.headers['Set-Cookie'].strip().startswith('sessionid-')
    assert 'HttpOnly' in resp.headers['Set-Cookie']
    assert 'Secure' in resp.headers['Set-Cookie']


def test_form_worklow_multiple_identical_status_with_journal_error(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st1'
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    app = get_app(pub)
    login(app, username='foo', password='foo')

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.allows_backoffice_access = False
    role.store()

    jump.by = [role.id]
    workflow.store()
    user.roles = [role.id]
    user.store()

    for _i in range(3):
        resp = app.post(formdata.get_url() + 'jump/trigger/XXX', status=302)
        formdata = formdef.data_class().get(formdata.id)
        formdata.evolution[-1].add_part(
            JournalWsCallErrorPart('summary', varname='x', label='label', url='http://test', data='data')
        )
        formdata.evolution[-1].add_part(JournalAssignationErrorPart('foo', 'foo'))
        formdata.store()

    # mark user as owner so it can check the UI
    formdata.user_id = user.id
    formdata.store()
    resp = app.get(formdata.get_url())
    assert resp.text.count('<li class="msg') == 1

    role.allows_backoffice_access = True
    role.store()
    resp = app.get(formdata.get_url(backoffice=True))
    assert len(resp.pyquery('div.msg')) == 3
    assert len(resp.pyquery('div.msg div.ws-error')) == 3
    assert len(resp.pyquery('div.msg div.assignation-error')) == 3


def test_form_data_keywords(pub):
    formdef = create_formdef()
    formdef.keywords = 'hello,world'
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert 'data-keywords="hello world"' in resp.text
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'data-keywords="hello world"' in resp.text
    resp = resp.form.submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1


def test_logged_errors(pub):
    Workflow.wipe()
    workflow = Workflow.get_default_workflow()
    workflow.id = '12'
    st1 = workflow.possible_status[0]
    jump = st1.add_action('jump', id='_jump', prepend=True)
    jump.id = '_jump'
    jump.status = 'rejected'
    jump.condition = {'type': 'django', 'value': 'a = b'}  # TemplateSyntaxError
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.workflow = workflow
    formdef.name = 'test'
    formdef.confirmation = False
    formdef.fields = []
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    resp = resp.form.submit('submit').follow()
    resp = resp.form.submit('submit')
    assert LoggedError.count() == 1

    # new expression, but raise the same exception (TemplateSyntaxError),
    # just update the created logged error
    jump.condition = {'type': 'django', 'value': 'b = c'}
    workflow.store()
    resp = app.get('/test/')
    resp = resp.form.submit('submit').follow()
    resp = resp.form.submit('submit')
    assert LoggedError.count() == 1

    error = list(
        LoggedError.select(
            [
                Equal(
                    'tech_id',
                    f'{formdef.id}-{workflow.id}-just_submitted-_jump-failed-to-evaluate-condition-'
                    'TemplateSyntaxError-could-not-parse-the-remainder-from',
                )
            ]
        )
    )[0]
    assert error.occurences_count == 2
    assert error.context == {
        'stack': [
            {
                'condition': 'b = c',
                'condition_type': 'django',
                'source_label': 'Automatic Jump',
                'source_url': 'http://example.net/backoffice/workflows/12/status/just_submitted/items/_jump/',
            }
        ]
    }

    assert LoggedError.count([Equal('formdef_id', str(formdef.id))]) == 1
    assert LoggedError.count([Equal('formdef_id', 'X')]) == 0

    assert LoggedError.count([Equal('workflow_id', '12')]) == 1
    assert LoggedError.count([Equal('workflow_id', 'X')]) == 0


def test_resubmit(pub):
    Workflow.wipe()

    user = create_user(pub)

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='string', varname='toto')]
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'form title bis'
    formdef2.enable_tracking_codes = True
    formdef2.fields = [
        fields.StringField(id='1', label='string', varname='titi'),
        fields.StringField(id='2', label='string', varname='toto'),
    ]
    formdef2.store()

    wf = Workflow(name='resubmit')
    st1 = wf.add_status('Status1')
    st2 = wf.add_status('Status2')

    resubmit = st1.add_action('resubmit', id='_resubmit')
    resubmit.by = ['_submitter']
    resubmit.formdef_slug = formdef2.url_name

    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id

    register_comment = st2.add_action('register-comment', id='_register')
    register_comment.comment = '<p><a href="[resubmit_formdata_draft_url]">new draft</a></p>'

    wf.store()

    formdef.workflow_id = wf.id
    formdef.store()

    formdef2.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.data = {'1': 'XXX'}
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_resubmit')
    resp = resp.follow()
    assert 'new draft' in resp.text
    assert formdef2.data_class().select()[0].status == 'draft'
    assert formdef2.data_class().select()[0].data.get('1') is None
    assert formdef2.data_class().select()[0].data.get('2') == 'XXX'
    resp = resp.click('new draft')
    resp = resp.follow()
    assert resp.forms[1]['f2'].value == 'XXX'

    # anonymous
    formdef2.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/form-title/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()
    resp = resp.form.submit('button_resubmit')
    resp = resp.follow()
    assert 'new draft' in resp.text
    assert formdef2.data_class().select()[0].status == 'draft'
    assert formdef2.data_class().select()[0].data.get('1') is None
    assert formdef2.data_class().select()[0].data.get('2') == 'foo'
    resp = resp.click('new draft')
    resp = resp.follow()
    assert resp.forms[1]['f2'].value == 'foo'


def test_form_custom_select_template(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='1', label='select', required='required', varname='foo', items=['Foo', 'Bar', 'Baz']
        )
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert 'TEST TEMPLATE' not in resp.text
    formdef.fields[0].extra_css_class = 'template-test'
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert 'TEST TEMPLATE' in resp.text
    # make sure request is available in context
    assert '<!-- backoffice: False -->' in resp.text
    assert '<!-- backoffice compat: False -->' in resp.text

    # test for substitution variables being available
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'example_url', 'http://remote.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = get_app(pub).get('/test/')
    assert 'substitution variable: http://remote.example.net/' in resp.text


def test_form_status_appearance_keywords(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='1', label='select', required='required', varname='foo', items=['Foo', 'Bar', 'Baz']
        )
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    root = PublicFormStatusPage(formdef, formdata, register_workflow_subdirs=False)
    template_names = root.get_formdef_template_variants(root.status_templates)
    assert list(template_names) == root.status_templates

    formdef.appearance_keywords = 'foobar plop'
    formdef.store()

    template_names = root.get_formdef_template_variants(root.status_templates)
    assert list(template_names) == [
        'wcs/front/appearance-foobar/formdata_status.html',
        'wcs/front/appearance-plop/formdata_status.html',
        'wcs/front/formdata_status.html',
        'wcs/appearance-foobar/formdata_status.html',
        'wcs/appearance-plop/formdata_status.html',
        'wcs/formdata_status.html',
    ]

    resp = get_app(pub).get('/test/')
    assert 'class="quixote foobar plop"' in resp.text


def test_user_global_action(pub):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]

    resp = app.get(formdata.get_url())
    assert 'button-action-1' not in resp.text

    trigger.roles = ['_submitter']
    workflow.store()

    WorkflowTrace.wipe()
    resp = app.get(formdata.get_url())
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')

    resp = app.get(formdata.get_url())
    assert 'HELLO WORLD GLOBAL ACTION' in resp.text
    assert resp.pyquery('.endpoint .user').text() == 'User Name'
    assert formdef.data_class().get(formdata.id).status == 'wf-finished'
    trace_event, trace_action = WorkflowTrace.select_for_formdata(formdata)[:2]
    assert (
        trace_action.get_base_url(workflow, trace_action.status_id, trace_event)
        == 'http://example.net/backoffice/workflows/2/global-actions/1/'
    )


def test_user_global_action_same_status_store(pub):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1'),
    ]
    action = workflow.add_global_action('FOOBAR')
    jump = action.add_action('jump')
    jump.status = 'new'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    new_status = workflow.possible_status[1]

    setbo = new_status.add_action('set-backoffice-fields', prepend=True)
    setbo.fields = [{'field_id': 'bo1', 'value': '123'}]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['bo1'] == '123'

    # change global action
    setbo.fields = [{'field_id': 'bo1', 'value': '321'}]
    workflow.store()

    resp = app.get(formdata.get_url())
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')  # click global action

    # check status actions are rerun
    resp = app.get(formdata.get_url())
    assert formdef.data_class().get(formdata.id).status == 'wf-new'
    assert formdef.data_class().get(formdata.id).data['bo1'] == '321'


def test_anonymous_user_global_action(pub):
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    formdef.enable_tracking_codes = True
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]

    app.cookiejar.clear()

    resp = app.get('/')
    resp.forms[0]['code'] = formdata.tracking_code
    resp = resp.forms[0].submit().follow().follow()

    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')

    resp = app.get(formdata.get_url())
    assert 'HELLO WORLD GLOBAL ACTION' in resp.text
    assert formdef.data_class().get(formdata.id).status == 'wf-finished'


def test_condition_on_action(pub, emails):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    # change email subjects to differentiate them
    workflow.possible_status[0].items[0].subject = 'New form ([name])'
    workflow.possible_status[0].items[1].subject = 'New form2 ([name])'
    workflow.id = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test condition on action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert not emails.get('New form (test condition on action)')  # no receiver
    assert emails.get('New form2 (test condition on action)')  # submitter

    emails.empty()

    workflow.possible_status[0].items[1].condition = {'type': 'django', 'value': 'False'}
    workflow.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert not emails.get('New form2 (test condition on action)')

    # check with a condition on field data
    formdef.fields = [fields.StringField(id='0', label='string', varname='foobar')]
    formdef.store()
    workflow.possible_status[0].items[1].condition = {'type': 'django', 'value': 'form_var_foobar'}
    workflow.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp.form['f0'] = ''
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert not emails.get('New form2 (test condition on action)')

    # check with condition evaluating positively
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'toto'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert emails.get('New form2 (test condition on action)')


def test_user_global_action_along_form(pub):
    # check it's possible to click on a global action button even if there's a
    # form with required fields.
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    jump = action.add_action('jump')
    jump.status = 'finished'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    status = workflow.get_status('new')
    display_form = status.add_action('form', id='_x')
    display_form.id = '_x'
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(fields.StringField(id='1', label='blah', required='required'))

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]

    resp = app.get(formdata.get_url())
    assert resp.form[f'fxxx_{display_form.id}_1'].attrs['aria-required'] == 'true'
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')

    resp = app.get(formdata.get_url())
    assert 'HELLO WORLD GLOBAL ACTION' in resp.text
    assert formdef.data_class().get(formdata.id).status == 'wf-finished'


def test_user_global_action_specific_statuses(pub):
    user = create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO WORLD GLOBAL ACTION'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    assert 'button-action-1' in resp.text

    trigger.statuses = ['accepted']
    workflow.store()

    resp = app.get(formdata.get_url())
    assert 'button-action-1' not in resp.text

    formdata.jump_status('accepted')
    formdata.store()

    resp = app.get(formdata.get_url())
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')

    resp = app.get(formdata.get_url())
    assert 'HELLO WORLD GLOBAL ACTION' in resp.text


def test_email_actions(pub, emails):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    # change email subjects to differentiate them
    workflow.possible_status[0].items[0].subject = 'New form ([name])'
    workflow.possible_status[0].items[1].subject = 'New form2 ([name])'
    workflow.possible_status[0].items[
        1
    ].body = 'Hello; {% action_button "do-accept" label="Accepté!" %} Adiós.'
    workflow.possible_status[1].items[1].identifier = 'do-accept'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test email action'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    action_url = re.findall(r'http.* ', email_data['payload'])[0].strip()
    assert '/actions/' in action_url
    if docutils:
        assert len(email_data['payloads']) == 2
        assert action_url in force_str(email_data['payloads'][1])

    app = get_app(pub)
    resp = app.get(action_url)
    assert 'Accepté!' in resp.text
    resp = resp.form.submit()
    assert 'The action has been confirmed.' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-accepted'
    assert [x.event for x in formdata.get_workflow_traces() if x.event][-1] == 'email-button'
    assert list(formdata.iter_evolution_parts(klass=JumpEvolutionPart))[0].identifier == 'do-accept'

    # action token has been used, it will now return a custom 404
    resp = app.get(action_url, status=404)
    assert 'This action link has already been used or has expired.' in resp.text

    # check against independently changed status, it should also return a
    # custom 404.
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    action_url = re.findall(r'http.* ', email_data['payload'])[0].strip()
    formdata = formdef.data_class().select()[0]
    formdata.jump_status('rejected')
    app = get_app(pub)
    resp = app.get(action_url, status=404)
    assert 'This action link has already been used or has expired.' in resp.text

    # check action link referencing a deleted formdata
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    action_url = re.findall(r'http.* ', email_data['payload'])[0].strip()
    formdata = formdef.data_class().select()[0]
    formdata.remove_self()
    app = get_app(pub)
    resp = app.get(action_url, status=404)
    assert 'This action link is no longer valid as the attached form has been removed.' in resp.text

    # check action link referencing a formdata with an invalid/unknown status
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    action_url = re.findall(r'http.* ', email_data['payload'])[0].strip()
    formdata = formdef.data_class().select()[0]
    formdata.status = 'wf-abc'
    formdata.store()
    app = get_app(pub)
    resp = app.get(action_url, status=404)
    assert 'This action link is no longer valid' in resp.text

    # two buttons on the same line, two urls
    workflow.possible_status[0].items[
        1
    ].body = '{% action_button "ok" label="OK" %} {% action_button "ko" label="KO" %} '
    workflow.store()
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    assert len(re.findall(r'http.*?\s', email_data['payload'])) == 2
    if docutils:
        html_payload = email_data.payloads[1].decode()
        assert html_payload.count('button link start') == 1
        assert html_payload.count('button link inner start') == 1
        assert html_payload.count('button link inner end') == 1
        assert html_payload.count('button link end') == 1
        assert html_payload.count('/actions/') == 2

    # custom messages
    workflow.possible_status[0].items[
        1
    ].body = 'Hello {% action_button "do-accept" label="ok" message="FOOmessageBAR" done_message="FOOdoneBAR" %} bye.'
    workflow.store()
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    email_data = emails.get('New form2 (test email action)')
    action_url = re.findall(r'http.* ', email_data['payload'])[0].strip()
    app = get_app(pub)
    resp = app.get(action_url)
    assert 'FOOmessageBAR' in resp.text
    resp = resp.form.submit()
    assert 'FOOdoneBAR' in resp.text

    # multiple buttons
    if docutils:
        workflow.possible_status[0].items[
            1
        ].body = '''test same line
        {% action_button "ok" label="OK" %} {% action_button "ko" label="KO" %}

        test not same line but together
        {% action_button "ok" label="OK" %}
        {% action_button "ko" label="KO" %}

        test separate lines
        {% action_button "ok" label="OK" %}

        {% action_button "ko" label="KO" %}
        end
        '''
        workflow.store()
        emails.empty()
        formdef.data_class().wipe()
        app = login(get_app(pub), username='foo', password='foo')
        resp = app.get(formdef.get_url())
        resp = resp.form.submit('submit')
        resp = resp.form.submit('submit')
        email_data = emails.get('New form2 (test email action)')
        assert len(re.findall(r'http.*?\s', email_data['payload'])) == 6
        html_payload = email_data.payloads[1].decode()
        assert html_payload.count('/actions/') == 6
        assert html_payload.count('button link start') == 4  # 2x2 buttons + 2x1 button

    # check with missing label parameter
    LoggedError.wipe()
    workflow.possible_status[0].items[1].body = '''{% action_button "ok" %}'''
    workflow.store()
    emails.empty()
    formdef.data_class().wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert not emails.get('New form2 (test email action)')
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Error in body template, mail could not be generated'
    assert LoggedError.select()[0].exception_message == '{% action_button %} requires a label parameter'


def test_card_email_actions(pub, emails):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'

    workflow.possible_status[0].items[0].subject = None  # disable first mail
    workflow.possible_status[0].items[1].subject = 'New card'
    workflow.possible_status[0].items[1].body = 'XXX {% action_button "do-accept" label="Accepté!" %}'
    workflow.possible_status[0].items[1].to = ['test@example.net']  # force recipient
    workflow.possible_status[1].items[1].identifier = 'do-accept'
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test email action'
    carddef.fields = []
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_receiver': 1}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()
    carddata.perform_workflow()
    carddata.store()

    email_data = emails.get('New card')
    action_url = re.findall(r'\shttp.*\s', email_data['payload'])[0].strip()
    assert '/actions/' in action_url
    if docutils:
        assert len(email_data['payloads']) == 2
        assert action_url in force_str(email_data['payloads'][1])

    headers = email_data.email.extra_headers
    assert 'Message-ID' in headers
    assert 'References' not in headers
    assert 'In-Reply-To' not in headers
    hostname = 'example.net'
    expt_id = fr'<wcs-carddata-{carddef.id}-{carddata.id}.[0-9]{{8}}\.[0-9]{{6}}\.[^@]+@{hostname}>'
    assert re.match(expt_id, headers['Message-ID'])

    app = get_app(pub)
    resp = app.get(action_url)
    assert 'Accepté!' in resp.text
    resp = resp.form.submit()
    assert 'The action has been confirmed.' in resp.text
    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert carddata.status == 'wf-accepted'


def test_email_temporary_form_button(pub, emails):
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.emails_to_members = True
    role.store()
    user = create_user(pub)
    user.roles = [role.id]
    user.store()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'

    workflow.possible_status[0].items[0].subject = None  # disable first mail
    workflow.possible_status[0].items[1].subject = 'New form'
    workflow.possible_status[0].items[1].body = 'Hello;\n{% temporary_access_button label="Open" %}\nAdiós.'
    workflow.possible_status[0].items[1].to = ['test@example.net']  # force recipient
    workflow.possible_status[1].items[1].identifier = 'do-accept'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test email form button'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    email_data = emails.get('New form')
    form_url = re.findall(r'\shttp.*\s', email_data['payload'])[0].strip()
    if docutils:
        assert len(email_data['payloads']) == 2
        assert form_url in force_str(email_data['payloads'][1])

    app = get_app(pub)
    resp = app.get(form_url).follow()
    assert 'The form has been recorded' in resp.text

    # check with missing label parameter
    LoggedError.wipe()
    workflow.possible_status[0].items[1].body = 'Hello;\n{% temporary_access_button %}\nAdiós.'
    workflow.store()
    formdef.refresh_from_storage()
    emails.empty()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()
    assert not emails.get('New form')
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Error in body template, mail could not be generated'
    assert (
        LoggedError.select()[0].exception_message
        == '{% temporary_action_button %} requires a label parameter'
    )


def test_manager_public_access(pub):
    user, manager = create_user_and_admin(pub)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.allows_backoffice_access = True
    role.store()

    manager.is_admin = False
    manager.roles = [role.id]
    manager.store()
    assert manager.can_go_in_backoffice()

    formdef = create_formdef()
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    # user access to own formdata
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    assert 'The form has been recorded' in resp.text

    # agent access to formdata
    app = login(get_app(pub), username='admin', password='admin')
    resp = app.get(formdata.get_url())
    assert resp.location == formdata.get_url(backoffice=True)
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    # agent access to an unauthorized formdata
    formdef.workflow_roles = {'_receiver': None}
    formdef.store()
    resp = app.get(formdata.get_url(), status=403)

    # agent access via a tracking code (stays in frontoffice)
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.enable_tracking_codes = True
    formdef.store()

    code = TrackingCode()
    code.formdata = formdata
    code.store()

    resp = app.get('/code/%s/load' % code.id)
    resp = resp.follow()  # -> /test/1/
    assert 'backoffice' not in resp.request.path
    assert 'The form has been recorded' in resp.text

    # authorized access but not backoffice access
    app = login(get_app(pub), username='admin', password='admin')  # reset session
    resp = app.get(formdata.get_url())
    assert resp.location == formdata.get_url(backoffice=True)  # check tracking code is no longer effective
    role.allows_backoffice_access = False
    role.store()
    resp = app.get(formdata.get_url())
    assert 'The form has been recorded' in resp.text

    # agent access to own formdata (stays in frontoffice)
    formdata = formdef.data_class()()
    formdata.user_id = manager.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url())
    assert 'The form has been recorded' in resp.text


def test_form_and_category_same_slug(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = []
    formdef.store()

    # check we get to the form, not the category
    resp = get_app(pub).get('/foobar/')
    assert resp.form


def test_field_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='Bar',
            size='40',
            required='required',
            condition={'type': 'django', 'value': '1'},
        ),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            condition={'type': 'django', 'value': '0'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' not in resp.form.fields
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' not in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Bar' in [x.text for x in resp.pyquery('p.label')]
    assert 'Foo' not in [x.text for x in resp.pyquery('p.label')]


def test_field_unicode_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='2nd page'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.PageField(id='3', label='1st page'),
        fields.StringField(
            id='4',
            label='Baz',
            size='40',
            required='required',
            varname='baz',
            condition={'type': 'django', 'value': 'form_var_bar == "éléphant"'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'f4' not in resp.form.fields

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'éléphant'
    resp = resp.form.submit('submit')
    assert 'f4' in resp.form.fields


def test_field_unicode_condition_contains_in_list(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='2nd page'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.PageField(id='3', label='1st page'),
        fields.StringField(
            id='4',
            label='Baz',
            size='40',
            required='required',
            varname='baz',
            condition={'type': 'django', 'value': 'form_var_bar in "éléphant"|split'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'f4' not in resp.form.fields

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'éléphant'
    resp = resp.form.submit('submit')
    assert 'f4' in resp.form.fields


def test_field_unicode_condition_contains_in_string(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='2nd page'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.PageField(id='3', label='1st page'),
        fields.StringField(
            id='4',
            label='Baz',
            size='40',
            required='required',
            varname='baz',
            condition={'type': 'django', 'value': '"éléphant" in form_var_bar'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'f4' not in resp.form.fields

    resp = get_app(pub).get('/foo/')
    resp.form['f1'] = 'éléphant'
    resp = resp.form.submit('submit')
    assert 'f4' in resp.form.fields


def test_field_unicode_condition_in_array(pub):
    Workflow.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemsField(
            id='1',
            label='items',
            required='required',
            varname='foo',
            items=['Pomme', 'Poire', 'Pêche', 'Abricot'],
        ),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3',
            label='Baz',
            size='40',
            required='required',
            varname='baz',
            condition={'type': 'django', 'value': '"Pêche" in form_var_foo'},
        ),
        fields.CommentField(id='4', label='{{form_var_foo}}'),
        fields.CommentField(id='5', label='{% if "Pêche" in form_var_foo %}CHECK OK{% endif %}'),
    ]

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    display1 = st1.add_action('displaymsg')
    display1.message = 'Message {% if "Pêche" in form_var_foo %}CHECK OK{% endif %}'
    display1.to = []
    workflow.store()

    formdef.workflow = workflow
    formdef.store()

    resp = get_app(pub).get('/foo/')
    resp.form['f1$elementpoire'].checked = True
    resp = resp.form.submit('submit')
    assert 'f3' not in resp.form.fields

    resp = get_app(pub).get('/foo/')
    resp.form['f1$elementpoire'].checked = True
    resp.form['f1$elementpeche'].checked = True
    resp = resp.form.submit('submit')
    assert 'f3' in resp.form.fields  # check it's ok in field condition
    resp.form['f3'] = 'hop'
    assert '>Poire, Pêche<' in resp.text  # check it's displayed correctly
    assert 'CHECK OK' in resp.text  # check it's ok in template condition
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()
    assert '<p>Message CHECK OK</p>' in resp.text  # check it's ok in workflow template


def test_form_edit_and_backoffice_field_change(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='bo field 1', varname='plop'),
    ]
    st1 = workflow.add_status('Status1', 'st1')
    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [{'field_id': 'bo1', 'value': '{{form_var_foo}}'}]
    setbo2 = st1.add_action('set-backoffice-fields')
    setbo2.fields = [{'field_id': 'bo1', 'value': 'foo{{ form_var_plop }}'}]
    jump = st1.add_action('jump')
    jump.status = 'st2'

    st2 = workflow.add_status('Status2', 'st2')

    editable = st2.add_action('editable', id='_editable')
    editable.by = ['_submitter']
    editable.status = st1.id
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f1'] = 'bar'
    resp = resp.form.submit('submit')  # -> page 2
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'The form has been recorded' in resp.text

    data_id = formdef.data_class().select()[0].id
    assert formdef.data_class().get(data_id).data['bo1'] == 'foobar'

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/%s/' % data_id)
    assert 'button_editable-button' in resp.text

    resp = resp.form.submit('button_editable')
    resp = resp.follow()
    assert resp.form['f1'].value == 'bar'
    resp.form['f1'].value = 'baz'
    resp = resp.form.submit('submit')  # -> page 2
    resp = resp.form.submit('submit').follow()  # -> saved

    assert formdef.data_class().get(data_id).data['bo1'] == 'foobaz'


def test_backoffice_fields_just_after_conditional_form_submit(pub):
    """
    simulate selection of a structured list via condition on form,
    followed by an evaluation on workflow in order to get structured value
    from the selected list.
    ie: test unfeed on ConditionVars
    """
    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='first text', varname='both_text'),
        fields.StringField(id='bo2', label='first more', varname='both_more'),
    ]

    st1 = workflow.add_status('Status1', 'st1')
    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [
        {'field_id': 'bo1', 'value': '{{ form_var_listA }} vs {{ form_var_listB }}'},
        {'field_id': 'bo2', 'value': '{{ form_var_listA_more }} vs {{ form_var_listB_more }}'},
    ]
    workflow.store()

    items_A = [{'id': '1', 'text': 'A1', 'more': 'moreA1'}]
    items_B = [{'id': '1', 'text': 'B1', 'more': 'moreB1'}, {'id': '2', 'text': 'B2', 'more': 'moreB2'}]
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', varname='choice', items=['A', 'B'], label='list to choice'),
        fields.ItemField(
            id='2',
            varname='listA',
            label='list A',
            data_source={'type': 'jsonvalue', 'value': json.dumps(items_A)},
            condition={'type': 'django', 'value': 'form_var_choice_raw == "A"'},
        ),
        fields.ItemField(
            id='3',
            varname='listB',
            label='list B',
            data_source={'type': 'jsonvalue', 'value': json.dumps(items_B)},
            condition={'type': 'django', 'value': 'form_var_choice_raw == "B"'},
        ),
    ]
    formdef.confirmation = False
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    create_user_and_admin(pub)
    resp = get_app(pub).get('/test/')

    resp.form['f1'].value = 'B'
    resp.form['f2'].value = '1'
    resp.form['f3'].value = '2'

    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'B'
    assert formdata.data.get('2') is None
    assert formdata.data['3'] == '2'

    assert formdata.data['bo1'] == 'None vs B2'
    assert formdata.data['bo2'] == 'vs moreB2'


def test_backoffice_fields_just_after_conditional_form_edit_action(pub):
    """
    test unfeed on ConditionVars within edit context
    """
    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='both text', varname='both_text'),
        fields.StringField(id='bo2', label='both more', varname='both_more'),
    ]

    st1 = workflow.add_status('Status1', 'st1')
    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [
        {'field_id': 'bo1', 'value': '{{ form_var_listA }} vs {{ form_var_listB }}'},
        {'field_id': 'bo2', 'value': '{{ form_var_listA_more }} vs {{ form_var_listB_more }}'},
    ]
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter']
    editable.status = st1.id
    workflow.store()

    items_A = [{'id': '1', 'text': 'A1', 'more': 'moreA1'}]
    items_B = [{'id': '1', 'text': 'B1', 'more': 'moreB1'}, {'id': '2', 'text': 'B2', 'more': 'moreB2'}]
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', varname='choice', items=['A', 'B'], label='list to choice'),
        fields.ItemField(
            id='2',
            varname='listA',
            label='list A',
            data_source={'type': 'jsonvalue', 'value': json.dumps(items_A)},
            condition={'type': 'django', 'value': 'form_var_choice_raw == "A"'},
        ),
        fields.ItemField(
            id='3',
            varname='listB',
            label='list B',
            data_source={'type': 'jsonvalue', 'value': json.dumps(items_B)},
            condition={'type': 'django', 'value': 'form_var_choice_raw == "B"'},
        ),
    ]
    formdef.confirmation = False
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    create_user(pub)
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')

    resp.form['f1'].value = 'B'
    resp.form['f2'].value = '1'
    resp.form['f3'].value = '2'

    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'B'
    assert formdata.data.get('2') is None
    assert formdata.data['3'] == '2'

    # check unfeed on FormPage::submitted()
    assert formdata.data['bo1'] == 'None vs B2'
    assert formdata.data['bo2'] == 'vs moreB2'

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/%s/' % formdata.id)
    assert 'button_editable-button' in resp.text

    resp = resp.form.submit('button_editable').follow()
    assert resp.form['f1'].value == 'B'
    resp.form['f1'].value = 'A'
    resp = resp.form.submit('submit').follow()  # -> saved
    assert 'The form has been recorded' in resp.text

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'A'
    assert formdata.data['2'] == '1'
    assert formdata.data.get('3') is None

    # check unfeed on FormPage::submitted_existing()
    assert formdata.data['bo1'] == 'A1 vs None'
    assert formdata.data['bo2'] == 'moreA1 vs'


def test_backoffice_fields_set_from_live(pub):
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='first text', varname='both_text'),
        fields.StringField(id='bo2', label='first more', varname='both_more'),
    ]

    st1 = workflow.add_status('Status1', 'st1')
    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [
        {'field_id': 'bo1', 'value': '{{ form_var.foo.attr }}'},
        {'field_id': 'bo2', 'value': '{{ form_var.foo.live.var.attr }}'},
    ]
    workflow.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', label='string', varname='foo', data_source=ds, display_disabled_items=True)
    ]
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    create_user_and_admin(pub)
    resp = get_app(pub).get('/test/')

    resp.form['f1'].value = '2'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == '2'
    assert formdata.data['bo1'] == 'attr1'
    assert formdata.data['bo2'] == 'attr1'


def test_choice_button_ignore_form_errors(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.roles = [logged_users_role().id]
    formdef.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [logged_users_role().id]
    commentable.required = True

    choice = st1.add_action('choice', id='_x1')
    choice.label = 'Submit'
    choice.by = [logged_users_role().id]
    choice.status = st2.id

    choice2 = st1.add_action('choice', id='_x2')
    choice2.label = 'Submit no check'
    choice2.by = [logged_users_role().id]
    choice2.status = st2.id
    choice2.ignore_form_errors = True

    wf.store()

    formdef.workflow = wf
    formdef.store()

    # no comment
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()

    resp = resp.form.submit('button_x1')
    assert 'There were errors processing your form.' in resp.text

    # comment
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()

    resp.form['comment'] = 'plop'
    resp = resp.form.submit('button_x1').follow()
    assert resp.pyquery('.comment').text() == 'plop'
    assert '<span class="status">Status2' in resp.text

    # no comment but no check
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()

    resp = resp.form.submit('button_x2').follow()
    assert '<span class="status">Status2' in resp.text


def test_form_comment_is_hidden_attribute(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1', varname='choice1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3',
            label='string 2',
            varname='choice2',
            condition={'type': 'django', 'value': 'form_var_choice1 == "1"'},
        ),
        fields.CommentField(
            id='5',
            label='this should not be displayed',
            condition={'type': 'django', 'value': 'False and form_var_choice2 == "???"'},
        ),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'] = '1'
    resp = resp.forms[0].submit('submit')
    comment = re.compile('.*comment-field.*"')
    assert resp.html.find('div', {'data-field-id': '5'})
    assert 'style="display: none"' in comment.search(resp.forms[0].text).group(0)
    resp = resp.forms[0].submit('previous')
    resp.forms[0]['f1'] = '2'
    resp = resp.forms[0].submit('submit')
    assert not resp.html.find('div', {'data-field-id': '5'})


@pytest.fixture
def create_formdata(pub):
    FormDef.wipe()

    data = [
        {'id': '1', 'text': 'un', 'more': 'foo'},
        {'id': '2', 'text': 'deux', 'more': 'bar'},
    ]
    ds = {
        'type': 'jsonvalue',
        'value': json.dumps(data),
    }
    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.fields = [
        fields.StringField(id='0', label='string', varname='toto_string'),
        fields.FileField(id='1', label='file', varname='toto_file'),
        fields.ItemField(
            id='2', label='item', required='optional', data_source=ds, varname='toto_item', hint='hint'
        ),
    ]
    source_formdef.store()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.enable_tracking_codes = True
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
        fields.FileField(id='1', label='file', varname='foo_file'),
        fields.ItemField(id='2', label='item', data_source=ds, varname='foo_item'),
    ]
    target_formdef.store()
    wf = Workflow(name='create-formdata')

    st1 = wf.add_status('New')
    st2 = wf.add_status('Resubmit')

    jump = st1.add_action('choice', id='_resubmit')
    jump.label = 'Resubmit'
    jump.by = ['_submitter']
    jump.status = st2.id

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.draft = True
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='{{ form_var_toto_string }}'),
        Mapping(field_id='1', expression='{{ form_var_toto_file_raw }}'),
        Mapping(field_id='2', expression='{{ form_var_toto_item_raw }}'),
    ]

    redirect = st2.add_action('redirect_to_url', id='_redirect')
    redirect.url = '{{ form_links_resubmitted.form_url }}'

    display = st2.add_action('displaymsg', id='_display')
    display.message = '''<div class="linked">{% if form_links_resubmitted %}
<p>Linked status: <span class="status">{{ form_links_resubmitted.form_status }}</span></p>
<p>Target formdata field: <span class="foo_string">{{ form_links_resubmitted.form_var_foo_string }}</span></p>
{% endif %}</div>'''
    display.to = []

    wf.store()
    source_formdef.workflow_id = wf.id
    source_formdef.store()
    return locals()


def test_create_formdata_anonymous_draft(create_formdata):
    create_formdata['source_formdef'].data_class().wipe()
    create_formdata['target_formdef'].data_class().wipe()

    app = get_app(create_formdata['pub'])
    resp = app.get('/source-form/')
    resp.form['f0'] = 'zob'
    resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f2'] = '2'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()
    assert create_formdata['target_formdef'].data_class().count() == 0
    resp = resp.form.submit('button_resubmit')
    assert create_formdata['target_formdef'].data_class().count() == 1
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data.get('0') == 'zob'

    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.status == 'draft'
    assert target_formdata.submission_context == {
        'orig_object_type': 'formdef',
        'orig_formdata_id': str(create_formdata['source_formdef'].data_class().select()[0].id),
        'orig_formdef_id': str(create_formdata['source_formdef'].id),
    }

    resp = resp.follow()
    resp = resp.follow()
    assert 'zob' in resp
    assert resp.click('test.txt').text == 'foobar'
    resp = resp.forms[1].submit('submit')  # -> validation
    resp = resp.forms[1].submit('submit')  # -> submission
    assert create_formdata['target_formdef'].data_class().count() == 1
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data.get('0') == 'zob'
    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.data.get('2') == '2'
    assert target_formdata.data.get('2_display') == 'deux'
    assert target_formdata.data.get('2_structured') == {'text': 'deux', 'id': '2', 'more': 'bar'}
    assert target_formdata.status == 'wf-new'

    source_formdata = create_formdata['source_formdef'].data_class().select()[0]
    resp = app.get(source_formdata.get_url())
    pq = resp.pyquery.remove_namespaces()
    assert pq('.linked .status').text() == 'New'
    assert pq('.linked .foo_string').text() == 'zob'


def test_create_formdata_anonymous_submitted(create_formdata):
    create_formdata['source_formdef'].data_class().wipe()
    create_formdata['target_formdef'].data_class().wipe()

    # submit directly
    create_formdata['wf'].get_status('2').items[0].draft = False
    create_formdata['wf'].store()

    app = get_app(create_formdata['pub'])
    resp = app.get('/source-form/')
    resp.form['f0'] = 'zob'
    resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f2'] = '2'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()
    assert create_formdata['target_formdef'].data_class().count() == 0
    resp = resp.form.submit('button_resubmit')
    assert create_formdata['target_formdef'].data_class().count() == 1
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data.get('0') == 'zob'

    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.status == 'wf-new'
    assert target_formdata.submission_context == {
        'orig_object_type': 'formdef',
        'orig_formdata_id': str(create_formdata['source_formdef'].data_class().select()[0].id),
        'orig_formdef_id': str(create_formdata['source_formdef'].id),
    }

    resp = resp.follow()
    assert 'New' in resp
    assert 'zob' in resp
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.data.get('1').get_content() == b'foobar'
    assert target_formdata.status == 'wf-new'

    source_formdata = create_formdata['source_formdef'].data_class().select()[0]
    resp = app.get(source_formdata.get_url())
    pq = resp.pyquery.remove_namespaces()
    assert pq('.linked .status').text() == 'New'
    assert pq('.linked .foo_string').text() == 'zob'


def test_create_formdata_empty_item_ds_with_id_parameter(pub, create_formdata):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/json',
    }
    data_source.id_parameter = 'id'
    data_source.store()
    create_formdata['source_formdef'].data_class().wipe()
    create_formdata['target_formdef'].data_class().wipe()
    create_formdata['source_formdef'].fields[2].data_source = {'type': 'foobar'}
    create_formdata['source_formdef'].store()
    create_formdata['target_formdef'].fields[2].data_source = {'type': 'foobar'}
    create_formdata['target_formdef'].store()

    with responses.RequestsMock() as rsps:
        data = {'data': create_formdata['data']}
        rsps.get('http://remote.example.net/json', json=data)

        app = get_app(create_formdata['pub'])
        resp = app.get('/source-form/')
        resp.form['f0'] = 'zob'
        resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
        resp = resp.form.submit('submit')  # -> validation
        resp = resp.form.submit('submit')  # -> submission
        resp = resp.follow()
        assert create_formdata['target_formdef'].data_class().count() == 0
        assert LoggedError.count() == 0
        resp = resp.form.submit('button_resubmit')
        assert LoggedError.count() == 0


def test_create_formdata_locked_prefill_parent(create_formdata):
    create_formdata['source_formdef'].data_class().wipe()
    create_formdata['target_formdef'].data_class().wipe()

    target_formdef = create_formdata['target_formdef']
    target_formdef.fields[0].prefill = {
        'type': 'string',
        'value': '{{form_parent_form_var_toto_string}}',
        'locked': True,
    }
    target_formdef.store()

    app = get_app(create_formdata['pub'])
    resp = app.get('/source-form/')
    resp.form['f0'] = 'zob'
    resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f2'] = '2'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()
    assert create_formdata['target_formdef'].data_class().count() == 0
    resp = resp.form.submit('button_resubmit')
    assert create_formdata['target_formdef'].data_class().count() == 1
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data.get('0') == 'zob'
    assert target_formdata.status == 'draft'

    resp = resp.follow()
    resp = resp.follow()
    assert resp.forms[1]['f0'].value == 'zob'
    assert resp.forms[1]['f0'].attrs['readonly']
    # try altering readonly field
    resp.forms[1]['f0'].value = 'xxx'
    resp = resp.forms[1].submit('submit')
    resp = resp.forms[1].submit('previous')
    assert resp.forms[1]['f0'].value == 'zob'
    resp = resp.forms[1].submit('submit')
    resp = resp.forms[1].submit('submit')
    assert create_formdata['target_formdef'].data_class().count() == 1
    target_formdata = create_formdata['target_formdef'].data_class().select()[0]
    assert target_formdata.data['0'] == 'zob'


def test_js_libraries(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True  # will force gadjo.js -> jquery-ui
    formdef.store()

    resp = get_app(pub).get('/test/', status=200)
    assert 'jquery.js' not in resp.text
    assert 'jquery.min.js' in resp.text
    assert 'qommon.forms.js' in resp.text

    pub.cfg['debug'] = {'debug_mode': True}
    pub.write_cfg()
    resp = get_app(pub).get('/test/', status=200)
    assert 'jquery.js' in resp.text
    assert 'jquery.min.js' not in resp.text
    assert 'jquery-ui.js' in resp.text
    assert 'jquery-ui.min.js' not in resp.text
    assert 'qommon.forms.js' in resp.text

    pub.cfg['branding'] = {'included_js_libraries': ['jquery.js']}
    pub.write_cfg()
    resp = get_app(pub).get('/test/', status=200)
    assert 'jquery.js' not in resp.text
    assert 'jquery.min.js' not in resp.text
    assert 'qommon.forms.js' in resp.text

    pub.cfg['branding'] = {'included_js_libraries': ['jquery.js', 'jquery-ui.js']}
    pub.write_cfg()
    resp = get_app(pub).get('/test/', status=200)
    assert 'jquery.js' not in resp.text
    assert 'jquery.min.js' not in resp.text
    assert 'qommon.forms.js' in resp.text

    pub.cfg['branding'] = {'included_js_libraries': ['jquery.js']}
    pub.write_cfg()
    formdef.enable_tracking_codes = False  # no popup, no jquery-ui (and no i18n.js)
    formdef.store()
    resp = get_app(pub).get('/test/', status=200)
    assert 'jquery-ui.js' not in resp.text
    assert 'jquery-ui.min.js' not in resp.text
    assert 'select2.js' not in resp.text
    assert 'i18n.js' not in resp.text

    # add autocomplete field
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='string',
            data_source={'type': 'jsonp', 'value': 'http://remote.example.net/jsonp'},
        ),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/', status=200)
    assert 'select2.js' in resp.text
    assert 'select2.css' in resp.text
    assert 'i18n.js' in resp.text

    pub.cfg['branding'] = {'included_js_libraries': ['jquery.js', 'select2.js']}
    pub.write_cfg()
    resp = get_app(pub).get('/test/', status=200)
    assert 'select2.js' not in resp.text
    assert 'select2.css' not in resp.text
    assert 'i18n.js' in resp.text


def test_after_submit_location(pub):
    create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [logged_users_role().id]
    commentable.required = True

    workflow.store()

    formdef = create_formdef()
    formdef.fields = []
    formdef.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')  # form page
    resp = resp.forms[0].submit('submit')  # confirmation page
    resp = resp.follow()

    resp.form['comment'] = 'plop'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/test/1/#action-zone'
    resp = resp.follow()

    display = st1.add_action('displaymsg')
    display.message = 'message-to-all'
    display.to = []
    workflow.store()

    resp.form['comment'] = 'plop'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/test/1/#'


def test_form_honeypot(pub):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string', required='optional')]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'plop'
    resp.forms[0]['f00'] = 'honey?'
    resp = resp.forms[0].submit('submit')
    assert 'Honey pots should be left untouched.' in resp
    assert formdef.data_class().count() == 0  # check no drafts have been saved


def test_form_honeypot_level2(pub):
    pub.load_site_options()
    pub.site_options.set('options', 'honeypots', 'level2')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string', required='optional')]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'plop'
    assert resp.forms[0]['f002'].value == ''
    resp = resp.forms[0].submit('submit')
    assert 'Honey pots should be left untouched.' in resp
    assert formdef.data_class().count() == 0  # check no drafts have been saved

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'plop'
    resp.forms[0]['f002'].value = resp.pyquery('form')[0].attrib['data-honey-pot-value']
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    assert formdef.data_class().count() == 1


def test_structured_workflow_options(pub):
    create_user_and_admin(pub)

    workflow = Workflow(name='test')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un', 'more': 'foo', 'not:allowed': 'a'},
                {'id': '2', 'text': 'deux', 'more': 'bar'},
            ]
        ),
    }
    workflow.variables_formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.ItemField(id='2', label='Test List', varname='bar', data_source=data_source),
        fields.ItemsField(id='3', label='Test Multi', varname='baz', data_source=data_source),
        fields.DateField(id='4', label='Date', varname='date'),
    ]
    st1 = workflow.add_status('Status1', 'st1')
    comment = st1.add_action('register-comment', id='_comment')
    comment.comment = 'Date option: {{ form_option_date }}'
    workflow.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='Test List',
            varname='bar',
            data_source={'type': 'jsonvalue', 'value': '{{ form_option_baz_structured|json_dumps }}'},
        ),
    ]
    formdef.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    # configure workflow options
    resp = login(get_app(pub), username='admin', password='admin').get('/backoffice/forms/%s/' % formdef.id)
    resp = resp.click('Options')
    resp.form['f1'].value = 'plop'
    resp.form['f2'].value = '1'
    resp.form['f3$element1'].checked = True
    resp.form['f4'].value = '2020-04-18'
    resp = resp.form.submit('submit')

    formdef = FormDef.get(formdef.id)
    assert formdef.workflow_options == {
        'foo': 'plop',
        'bar': '1',
        'bar_display': 'un',
        'bar_structured': {'id': '1', 'more': 'foo', 'text': 'un', 'not:allowed': 'a'},
        'baz': ['1'],
        'baz_display': 'un',
        'baz_structured': [{'id': '1', 'more': 'foo', 'text': 'un', 'not:allowed': 'a'}],
        'date': time.strptime('2020-04-18', '%Y-%m-%d'),
    }

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert resp.form['f1'].options == [('1', False, 'un')]
    resp = resp.form.submit('submit')  # form page
    resp = resp.form.submit('submit')  # confirmation page
    resp = resp.follow()

    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': '1',
        '1_display': 'un',
        '1_structured': {'id': '1', 'text': 'un', 'more': 'foo', 'not:allowed': 'a'},
    }
    assert '2020-04-18' in formdata.evolution[0].parts[1].content

    formdef_xml = ET.tostring(formdef.export_to_xml(include_id=False))
    assert b'not:allowed' not in formdef_xml


def test_exclude_self_condition(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(
            id='1',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_objects|filter_by:"foo"|filter_value:form_var_foo|exclude_self|count == 0',
                    },
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.StringField(id='1', label='string', varname='foo'),
    ]

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' not in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()

    # edit is ok
    resp = resp.form.submit('button_editable').follow()
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' not in resp

    # 2nd submission
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' in resp.text

    # submission with other value
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'other'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' not in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()

    # edit is ok
    resp = resp.form.submit('button_editable').follow()
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' in resp


def test_rich_commentable_action(pub):
    create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.roles = [logged_users_role().id]
    formdef.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = [logged_users_role().id]
    commentable.required = True

    choice = st1.add_action('choice', id='_x1')
    choice.label = 'Submit'
    choice.by = [logged_users_role().id]
    choice.status = st1.id
    wf.store()

    formdef.workflow = wf
    formdef.store()

    # comment
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submission
    resp = resp.follow()

    resp.form['comment'] = '<p>hello <i>world</i></p>'
    resp = resp.form.submit('button_x1').follow()
    assert resp.pyquery('div.comment').text() == 'hello world'
    assert '<p>hello <i>world</i></p>' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[-1].parts[-1].comment == '<p>hello <i>world</i></p>'

    # check link
    resp.form['comment'] = '<p>hello <a href="http://localhost/">link</a>.</p>'
    resp = resp.form.submit('button_x1').follow()
    assert '<p>hello <a href="http://localhost/" rel="nofollow">link</a>.</p>' in resp.text
    formdata = formdef.data_class().select()[0]
    assert (
        formdata.evolution[-1].parts[-1].comment
        == '<p>hello <a href="http://localhost/" rel="nofollow">link</a>.</p>'
    )

    # check unauthorized tags are removed
    resp.form['comment'] = '<p>hello <script>evil</script></p>'
    resp = resp.form.submit('button_x1').follow()
    assert '<p>hello evil</p>' in resp.text
    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[-1].parts[-1].comment == '<p>hello evil</p>'

    resp.form['comment'] = '<p></p>'  # left empty
    resp = resp.form.submit('button_x1')
    assert resp.pyquery('.error').text() == 'required field'

    resp.form['comment'] = '<p>   </p>'  # left ~empty
    resp = resp.form.submit('button_x1')
    assert resp.pyquery('.error').text() == 'required field'

    # url to links
    resp.form['comment'] = '<p>Here is the address: https://example.net</p>'
    resp = resp.form.submit('button_x1').follow()
    assert (
        '<p>Here is the address: <a href="https://example.net" rel="nofollow">https://example.net</a></p>'
        in resp.text
    )

    # test paragraphs are converted to newlines in plain text view
    resp.form['comment'] = '<p>hello</p><p>world</p>'
    resp = resp.form.submit('button_x1').follow()
    formdata = formdef.data_class().select()[0]
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_comment}}')
    assert tmpl.render(context) == 'hello\n\nworld'

    # test <br> are accepted and converted to single-newline in plain text view
    resp.form['comment'] = '<p>hello<br>world</p>'
    resp = resp.form.submit('button_x1').follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[-1].parts[-1].comment == '<p>hello<br>world</p>'
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_comment}}')
    assert tmpl.render(context) == 'hello\nworld'


def test_jumps_with_by_and_no_trigger(pub):
    FormDef.wipe()
    Workflow.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='xxx')
    role.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    jump = st1.add_action('jump')
    jump.status = 'st2'
    jump.by = [role.id]

    jump = st1.add_action('jump')
    jump.status = 'st3'
    jump.by = []

    workflow.add_status('Status2', 'st2')
    workflow.add_status('Status3', 'st3')
    workflow.store()

    formdef = create_formdef()
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')

    # it jumps to st2, as jump.by is only related to triggers
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-st2'


def test_user_filter_auto_custom_view(pub):
    user = create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.user_support = 'optional'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef.store()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.user_id = user.id
        carddata.just_created()
        carddata.store()

    carddata.user_id = None  # don't associate latest (baz) with user
    carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemField(id='0', label='item', varname='foo', data_source=ds),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert [x[2] for x in resp.form['f0'].options] == ['bar', 'baz', 'foo']

    formdef.fields[0].data_source['type'] = 'carddef:%s:_with_user_filter' % carddef.url_name
    formdef.store()
    resp = app.get(formdef.get_url())
    assert [x[2] for x in resp.form['f0'].options] == ['---']

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    assert [x[2] for x in resp.form['f0'].options] == ['bar', 'foo']


def test_go_to_backoffice(pub):
    formdef = create_formdef()
    app = get_app(pub)
    resp = app.get('/test/go-to-backoffice')
    assert resp.location.endswith('/backoffice/forms/%s/' % formdef.id)


def test_global_interactive_action(pub):
    user = create_user(pub)

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    display = action.add_action('displaymsg')
    display.message = 'This is a message'
    display.to = []

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.hide_submit_button = False
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields.append(
        fields.StringField(id='1', label='Test', varname='test', required='required')
    )
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO {{ form_workflow_form_blah_var_test }}'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    assert 'button-action-1' in resp.form.fields
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()  # -> error, empty action
    resp = resp.follow()  # -> back to form
    assert 'Configuration error: no available action.' in resp.text

    form_action.by = trigger.roles
    workflow.store()

    resp = app.get(formdata.get_url(backoffice=False))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert 'This is a message' in resp.text
    resp = resp.form.submit('submit')
    assert resp.pyquery(f'#form_error_fblah_{form_action.id}_1').text() == 'required field'
    resp.form[f'fblah_{form_action.id}_1'] = 'GLOBAL INTERACTIVE ACTION'
    resp = resp.form.submit('submit')
    assert resp.location == formdata.get_url(backoffice=False)
    resp = resp.follow()

    assert 'HELLO GLOBAL INTERACTIVE ACTION' in resp.text


def test_global_interactive_action_form_prefill(pub):
    user = create_user(pub)

    formdef = FormDef()
    formdef.name = 'test global action'
    formdef.fields = []

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    action = workflow.add_global_action('FOOBAR')

    form_action = action.add_action('form')
    form_action.varname = 'blah'
    form_action.hide_submit_button = False
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields = [
        fields.StringField(
            id='1',
            label='Test',
            varname='test',
            required='required',
            prefill={'type': 'string', 'value': 'aaa'},
        ),
        fields.StringField(
            id='2',
            label='Test2',
            varname='test2',
            required='required',
            prefill={'type': 'string', 'value': 'bbb'},
            condition={'type': 'django', 'value': 'False'},
        ),
    ]
    form_action.by = ['_submitter']
    register_comment = action.add_action('register-comment')
    register_comment.comment = 'HELLO {{ form_workflow_form_blah_var_test }}'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()
    assert resp.form[f'fblah_{form_action.id}_1'].value == 'aaa'  # prefill
    assert f'fblah_{form_action.id}_2' not in resp.form.fields  # conditioned out
    resp.form[f'fblah_{form_action.id}_1'].value = 'HELLO GLOBAL INTERACTIVE ACTION'
    resp = resp.form.submit('submit')
    assert resp.location == formdata.get_url(backoffice=False)
    resp = resp.follow()
    assert 'HELLO GLOBAL INTERACTIVE ACTION' in resp.text


def test_category_redirection(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test category redirection'
    formdef.fields = []
    formdef.store()

    get_app(pub).get(formdef.get_url())

    Category.wipe()
    cat = Category(name='foo')
    cat.store()

    cat2 = Category(name='bar')
    cat2.store()

    formdef.category_id = cat.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(formdef.get_url(), status=302)
    assert resp.location == 'http://example.net/foo/test-category-redirection/'
    resp = resp.follow()

    resp = get_app(pub).get(formdef.get_url() + '?test=toto', status=302)
    assert resp.location == 'http://example.net/foo/test-category-redirection/?test=toto'

    # missing trailing /
    resp = get_app(pub).get(formdef.get_url().rstrip('/'), status=302)
    assert resp.location == 'http://example.net/test-category-redirection/'
    resp = resp.follow()
    assert resp.location == 'http://example.net/foo/test-category-redirection/'

    # missing trailing / + query string
    resp = get_app(pub).get(formdef.get_url().rstrip('/') + '?test=toto', status=302)
    assert resp.location == 'http://example.net/test-category-redirection/?test=toto'
    resp = resp.follow()
    assert resp.location == 'http://example.net/foo/test-category-redirection/?test=toto'

    resp = get_app(pub).get('/bar/test-category-redirection/', status=302)
    assert resp.location == 'http://example.net/foo/test-category-redirection/'

    resp = get_app(pub).get('/bar/test-category-redirection/?x=y', status=302)
    assert resp.location == 'http://example.net/foo/test-category-redirection/?x=y'

    # check formdata is redirected to login
    resp = get_app(pub).get('/foo/test-category-redirection/%s/' % formdata.id, status=302)
    assert '/login/' in resp.location
    # but there's no redirection if used with the wrong category
    resp = get_app(pub).get('/bar/test-category-redirection/%s/' % formdata.id, status=404)

    resp = get_app(pub).get(formdata.get_url(), status=302)
    assert (
        urllib.parse.urlparse(resp.location).path
        == urllib.parse.urlparse(formdata.get_url(include_category=True)).path
    )
    resp = get_app(pub).get(formdata.get_url() + '?test=toto', status=302)
    assert (
        urllib.parse.urlparse(resp.location).path
        == urllib.parse.urlparse(formdata.get_url(include_category=True)).path
    )
    assert urllib.parse.urlparse(resp.location).query == 'test=toto'

    # check with formdef and category with same slug
    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.category_id = cat2.id
    formdef.store()

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.store()

    resp = get_app(pub).get('/bar/', status=302)  # redirect even if category with same slug
    assert resp.location == 'http://example.net/bar/bar/'
    resp = get_app(pub).get('/bar/bar/', status=200)  # get formpage when full url is used
    assert resp.pyquery('#steps')  # make sure it's a form page
    resp = get_app(pub).get('/bar/bar/bar/', status=404)  # do not allow to go deeper

    resp = get_app(pub).get(formdata2.get_url(), status=302)
    assert (
        urllib.parse.urlparse(resp.location).path
        == urllib.parse.urlparse(formdata2.get_url(include_category=True)).path
    )

    # check other pages are ok with and without category slug
    get_app(pub).get('/bar/qrcode', status=200)
    get_app(pub).get('/bar/bar/qrcode', status=200)

    # check POST are ok
    resp = get_app(pub).get('/bar/bar/', status=200)
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    # check another formdef in same category is ok
    formdef = FormDef()
    formdef.name = 'bar2'
    formdef.fields = []
    formdef.category_id = cat2.id
    formdef.store()

    resp = get_app(pub).get('/bar2/', status=302)
    assert resp.location == 'http://example.net/bar/bar2/'
    resp = get_app(pub).get('/bar/bar2/', status=200)
    assert resp.pyquery('#steps')  # make sure it's a form page


def test_form_edit_with_category(pub):
    Workflow.wipe()

    create_user(pub)

    Category.wipe()
    cat = Category(name='foobar')
    cat.store()

    formdef = create_formdef()
    formdef.category_id = cat.id
    formdef.data_class().wipe()

    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef.workflow_id = workflow.id
    formdef.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')

    resp = app.get(formdef.get_url(include_category=True))
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit

    resp = resp.follow().follow()
    resp = resp.form.submit('button_editable')
    assert 'wfedit' in resp.location
    resp = resp.follow()
    assert 'f1' in resp.form.fields


def test_form_edit_single_or_partial_pages(pub):
    user = create_user(pub)

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.StringField(id='2', label='field1'),
        fields.PageField(id='3', label='2nd page'),
        fields.StringField(id='4', label='field2'),
        fields.PageField(id='5', label='3rd page'),
        fields.StringField(id='6', label='field3'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {'2': 'a', '4': 'b', '6': 'c'}
    formdata.just_created()
    formdata.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')

    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == [
        '1st page',
        '2nd page',
        '3rd page',
    ]

    editable.operation_mode = 'single'
    editable.page_identifier = 'plop'
    workflow.store()

    # unknown page identifier, a 404 is raised
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow(status=404)

    # add identifier to second page, and edit it
    formdef.fields[2].varname = 'plop'
    formdef.store()

    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == ['2nd page']
    resp.form['f4'] = 'changed'
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Save Changes', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous[hidden][disabled]')
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'2': 'a', '4': 'changed', '6': 'c'}

    # change action to edit all pages starting at page 2
    editable.operation_mode = 'partial'
    workflow.store()

    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == ['2nd page', '3rd page']
    resp.form['f4'] = 'other change'
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Next', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous[hidden][disabled]')
    resp = resp.form.submit('submit')
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Save Changes', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous:not([hidden])')
    assert resp.pyquery('.buttons button.form-previous:not([disabled])')
    resp = resp.form.submit('previous')
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Next', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous[hidden][disabled]')
    resp = resp.form.submit('submit')
    resp.form['f6'] = 'last change'
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Save Changes', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous:not([hidden])')
    assert resp.pyquery('.buttons button.form-previous:not([disabled])')
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'2': 'a', '4': 'other change', '6': 'last change'}

    # make page 2 hidden
    formdef.fields[2].condition = {'type': 'django', 'value': 'false'}
    formdef.store()

    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow()
    assert [x.text for x in resp.pyquery('#steps .wcs-step--label-text')] == ['3rd page']
    assert [x.text for x in resp.pyquery('.buttons button')] == ['Save Changes', 'Previous', 'Cancel']
    assert resp.pyquery('.buttons button.form-previous[hidden]')
    assert resp.pyquery('.buttons button.form-previous[disabled]')
    resp.form['f6'] = 'another last change'
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'2': 'a', '4': 'other change', '6': 'another last change'}

    # also make page 3 hidden -> 404
    formdef.fields[4].condition = {'type': 'django', 'value': 'false'}
    formdef.store()
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow(status=404)

    # check single page mode with hidden page (also 404)
    editable.operation_mode = 'partial'
    workflow.store()
    formdef.store()
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button_editable').follow(status=404)


def test_form_edit_and_jump_on_submit(pub):
    wf = Workflow(name='edit and jump on submit')
    st0 = wf.add_status('Status0')
    st1 = wf.add_status('Status1')
    st2 = wf.add_status('Status2')
    button = st0.add_action('choice')
    button.by = ['_submitter', '_receiver']
    button.label = 'jump'
    button.status = st1.id
    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='string', varname='toto')]
    formdef.workflow_id = wf.id
    formdef.store()

    resp = get_app(pub).get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit').follow()

    resp = resp.form.submit(f'button{button.id}').follow()
    assert formdef.data_class().select()[0].status == f'wf-{st1.id}'

    resp = resp.form.submit(f'button{editable.id}').follow()
    resp.form['f1'] = 'test2'
    resp = resp.form.submit('submit')

    assert formdef.data_class().select()[0].status == f'wf-{st1.id}'
    assert formdef.data_class().select()[0].data['1'] == 'test2'


def test_form_html_titles(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='1', label='string', required='optional'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert resp.pyquery('title').text() == 'test - 1/2 - Filling'
    resp = resp.forms[0].submit('submit')  # -> validation
    assert resp.pyquery('title').text() == 'test - 2/2 - Validating'
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert resp.pyquery('title').text() == 'test #1-1'

    # without confirmation page, single page, no counter
    formdef.confirmation = False
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.pyquery('title').text() == 'test - Filling'

    # naming first page
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.pyquery('title').text() == 'test - 1st page'

    # multi pages
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', required='optional'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.pyquery('title').text() == 'test - 1/2 - 1st page'
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert resp.pyquery('title').text() == 'test - 2/2 - 2nd page'

    # multi pages and confirmation page
    formdef.confirmation = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.pyquery('title').text() == 'test - 1/3 - 1st page'
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert resp.pyquery('title').text() == 'test - 2/3 - 2nd page'
    resp = resp.forms[0].submit('submit')  # -> validation
    assert resp.pyquery('title').text() == 'test - 3/3 - Validating'


def test_only_one_check(pub):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field')]
    formdef.only_allow_one = True
    formdef.store()

    formdef.data_class().wipe()

    for i in range(5):
        resp = get_app(pub).get('/form-title/')
        resp.form['f1'] = 'test'
        resp = resp.form.submit('submit')  # -> validation
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().count() == (i + 1)

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/form-title/')
    resp.form['f1'] = 'test2'
    resp = resp.form.submit('submit')  # -> validation
    # draft has been saved
    assert len([x for x in formdef.data_class().select() if x.is_draft()]) == 1

    # the draft doesn't prevent a new form being completed
    resp = app.get('/form-title/')
    resp.form['f1'] = 'test2'
    resp = resp.form.submit('submit')  # -> validation (1st form)

    resp2 = app.get('/form-title/')
    resp2.form['f1'] = 'test2'
    resp2 = resp2.form.submit('submit')  # -> validation (2nd form)

    resp = resp.form.submit('submit')  # -> submit (1st form)

    # check workflow has been run
    formdata_id = resp.location.split('/')[-2]
    formdata = formdef.data_class().get(formdata_id)
    assert formdata.status == 'wf-new'

    resp2 = resp2.form.submit('submit')  # -> submit (2nd form)
    assert resp.location == resp2.location

    # check there's no leftover
    latest = formdef.data_class().select([NotEqual('status', 'draft')], order_by='-id')[0]
    assert str(latest.id) == str(formdata_id)

    # when there's a form, redirect to it
    resp = app.get('/form-title/')
    resp = resp.follow()
    assert '>test2<' in resp.text


def test_form_errors_summary(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'false'}, 'error_message': 'You shall not pass.'}
            ],
        ),
        fields.StringField(id='1', label='string1', required='required'),
        fields.StringField(id='2', label='string2', required='optional'),
    ]

    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'You shall not pass.' in resp.pyquery('.errornotice').text()
    assert 'The following field has an error: string1' in resp.pyquery('.errornotice').text()

    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')
    assert 'You shall not pass.' in resp.pyquery('.errornotice').text()
    assert 'The following field has an error:' not in resp.pyquery('.errornotice').text()

    # remove post condition
    formdef.fields[0].post_conditions = []
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'The following field has an error: string1' in resp.pyquery('.errornotice').text()

    # check plurals
    formdef.fields[2].required = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'The following fields have an error: string1, string2' in resp.pyquery('.errornotice').text()
    for error_link in [x.attrib['href'] for x in resp.pyquery('.errornotice a')]:
        assert resp.pyquery(error_link)

    # check block
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test1'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef.fields.append(fields.BlockField(id='3', label='testblock', block_slug='foobar', max_items='3'))
    formdef.store()

    for label_display in ('normal', 'subtitle', 'hidden'):
        formdef.fields[-1].label_display = label_display
        formdef.store()

        resp = get_app(pub).get('/test/')
        resp.forms[0]['f1'] = 'foo'
        resp.forms[0]['f2'] = 'foo'
        resp = resp.forms[0].submit('submit')
        assert 'The following field has an error: testblock' in resp.pyquery('.errornotice').text()
        for error_link in [x.attrib['href'] for x in resp.pyquery('.errornotice a')]:
            assert resp.pyquery(error_link)

    # check there's a single link to block if there are errors in multiple rows
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp.forms[0]['f2'] = 'foo'
    resp.forms[0]['f3$element0$f123'] = 'foo'
    resp.forms[0]['f3$element0$f234'] = 'bar'
    resp = resp.form.submit('f3$add_element')
    assert not resp.pyquery('.errornotice')
    resp.forms[0]['f3$element0$f234'] = ''
    resp.forms[0]['f3$element1$f123'] = 'foo'
    resp = resp.forms[0].submit('submit')
    assert 'The following field has an error: testblock' in resp.pyquery('.errornotice').text()
    assert resp.pyquery('.error').text() == 'required field required field '


def test_form_submit_no_csrf(pub):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.confirmation = False
    formdef.store()
    formdef.data_class().wipe()

    create_user(pub)
    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'hello'
    # get expected data
    form_data = {x: y for x, y in resp.form.submit_fields('submit')}
    # remove token values
    form_data['_form_id'] = 'xxx'
    form_data['_ajax_form_token'] = 'xxx'
    form_data['magictoken'] = 'xxx'
    # simulate call from remote/attacker site (form token prevents this)
    resp = app.post(formdef.get_url(), params=form_data)
    assert 'The form you have submitted is invalid.' in resp.text

    # with confirmation page
    formdef.confirmation = True
    formdef.store()
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'hello'
    resp = resp.form.submit('submit')
    # get expected data
    form_data = {x: y for x, y in resp.form.submit_fields('submit')}
    # remove token values
    form_data['_form_id'] = 'xxx'
    form_data['_ajax_form_token'] = 'xxx'
    form_data['magictoken'] = 'xxx'
    # simulate call from remote/attacker site (magictoken prevents this)
    resp = app.post(formdef.get_url(), params=form_data, status=302)
    assert resp.location == formdef.get_url()

    # with multiple pages
    formdef.confirmation = False
    formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.PageField(id='2', label='page2'),
        fields.StringField(id='3', label='string'),
    ]
    formdef.store()
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')
    resp.form['f3'] = 'hello'
    # get expected data
    form_data = {x: y for x, y in resp.form.submit_fields('submit')}
    # remove token values
    form_data['_form_id'] = 'xxx'
    form_data['_ajax_form_token'] = 'xxx'
    form_data['magictoken'] = 'xxx'

    # simulate call from remote/attacker site (magictokens prevents this)
    resp = app.post(formdef.get_url(), params=form_data, status=302)
    assert resp.location == formdef.get_url()


def test_form_submit_no_csrf_suddenly_single_page(pub):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.ComputedField(id='2', label='computed', varname='plop', value_template='{{ "plop" }}'),
        fields.PageField(
            id='3', label='page2', condition={'type': 'django', 'value': 'form_var_plop != "plop"'}
        ),
    ]
    formdef.confirmation = False
    formdef.store()
    formdef.data_class().wipe()

    create_user(pub)
    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit').follow()
    assert formdef.data_class().select()[0].status == 'wf-new'


def test_form_submit_timezone(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'timezone', 'Brazil/East')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    formdef = create_formdef()
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> done
    formdata = formdef.data_class().select()[0]
    assert formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Brazil/East')).strftime('%H:%M') in resp.text
    assert (
        formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Europe/Paris')).strftime('%H:%M') not in resp.text
    )

    pub.site_options.set('options', 'timezone', 'Europe/Paris')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(formdata.get_url())
    assert (
        formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Brazil/East')).strftime('%H:%M') not in resp.text
    )
    assert formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Europe/Paris')).strftime('%H:%M') in resp.text

    # do not crash on invalid timezone
    pub.site_options.set('options', 'timezone', 'invalid')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get(formdata.get_url())
    assert (
        formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Brazil/East')).strftime('%H:%M') not in resp.text
    )
    assert formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Europe/Paris')).strftime('%H:%M') in resp.text

    pub.site_options.set('options', 'timezone', 'Brazil/East')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.setup_timezone()
    assert formdata.get_static_substitution_variables()[
        'form_receipt_time'
    ] == formdata.receipt_time.astimezone(zoneinfo.ZoneInfo('Brazil/East')).strftime('%H:%M')
    timezone.deactivate()
