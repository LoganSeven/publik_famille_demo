import datetime
import os

import pytest
from django.utils.timezone import make_aware

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.qommon.template import Template
from wcs.tracking_code import TrackingCode
from wcs.wf.comment import WorkflowCommentPart

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import assert_current_page, create_formdef, create_user, get_displayed_tracking_code


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


def test_form_no_tracking_code(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = False
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert '<h3>Tracking code</h3>' not in resp.text


def test_form_no_tracking_code_variable(pub):
    create_user(pub)
    FormDef.wipe()
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(id='3', label='<p>xxx{{form_tracking_code|default:""}}yyy</p>'),
    ]
    formdef.store()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp.form['f1'] = 'foo'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {
        'result': 'error',
        'reason': 'missing data',
    }
    resp = resp.form.submit('submit')
    assert_current_page(resp, '2nd page')
    assert 'xxxyyy' in resp.text
    resp = resp.form.submit('submit')
    assert_current_page(resp, 'Validating')
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'1': 'foo'}
    assert data.tracking_code is None


def test_form_tracking_code(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].is_draft()
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].data['0'] == 'foobar'
    formdata_id = formdef.data_class().select()[0].id

    # check we can load the formdata as a draft
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()

    # check anonymous user can't get to it from the URL
    pub.session_manager.session_class.wipe()
    resp = get_app(pub).get('http://example.net/test/%s/' % formdata_id)
    assert resp.location.startswith('http://example.net/login')

    # or logged users that didn't enter the code:
    create_user(pub)
    login(get_app(pub), username='foo', password='foo').get(
        'http://example.net/test/%s/' % formdata_id, status=403
    )

    # check we can also get to it as a logged user
    pub.session_manager.session_class.wipe()
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code.lower()
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()

    # go back as anonymous
    pub.session_manager.session_class.wipe()
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()
    resp = resp.forms[1].submit('previous')
    assert resp.forms[1]['f0'].value == 'foobar'

    # check submitted form keeps the tracking code
    resp.forms[1]['f0'] = 'barfoo'
    resp = resp.forms[1].submit('submit')  # -> confirmation page
    resp = resp.forms[1].submit('submit')  # -> done
    resp = resp.follow()
    assert 'barfoo' in resp.text
    assert 'You can get back to this page using the following tracking code' in resp.text
    assert str(resp.html.find('p', {'id': 'tracking-code'}).a) == '<a name="tracking-code-display">%s</a>' % (
        tracking_code
    )
    assert formdef.data_class().count() == 1  # check the draft one has been removed
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].status == 'wf-new'
    assert formdef.data_class().select()[0].data['0'] == 'barfoo'
    formdata_id = formdef.data_class().select()[0].id

    # check we can still go back to it
    app = get_app(pub)
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert 'form_comment' in resp.text  # makes sure user is treated as submitter
    resp.forms[0]['comment'] = 'hello world'
    resp = resp.forms[0].submit()
    assert formdef.data_class().get(formdata_id).evolution[-1].get_plain_text_comment() == 'hello world'

    # check we can also use it with lowercase letters.
    app = get_app(pub)
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code.lower()
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()

    # check using /code/load?code= is not allowed
    resp = app.get('/code/load?code=ABC', status=405)

    # check posting to /code/load with an empty code gives a proper error
    resp = app.post('/code/load', status=400)
    assert 'Missing code parameter.' in resp.text


def test_form_tracking_code_js_order(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit').follow()
    assert 'You can get back to this page using the following tracking code' in resp.text
    # qommon.forms.js must be loaded first, to disable gadjo handling of foldable sections
    assert resp.text.index('qommon.forms.js') < resp.text.index('gadjo.js')


def test_form_tracking_code_verification(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='0', label='string1', required='optional'),
        fields.StringField(id='1', label='string2', required='optional'),
        fields.DateField(id='2', label='date', required='optional'),
        fields.ComputedField(
            id='3', label='computed', varname='computed', value_template='{{ "computed"|upper }}'
        ),
    ]
    formdef.enable_tracking_codes = True
    formdef.tracking_code_verify_fields = ['0', '1', '2', '3']
    formdef.store()

    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    resp.forms[0]['f0'] = 'foobar1'
    resp.forms[0]['f1'] = 'foobar 2'
    resp.forms[0]['f2'] = '2022-01-01'
    resp = resp.forms[0].submit('submit')
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].is_draft()
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].data['0'] == 'foobar1'
    assert formdef.data_class().select()[0].data['1'] == 'foobar 2'
    assert formdef.data_class().select()[0].data['2'].tm_year == 2022
    assert formdef.data_class().select()[0].data['3'] == 'COMPUTED'
    formdata = formdef.data_class().select()[0]
    formdata_id = formdata.id

    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.text.count('Access rights verification') == 2  # <title> and body
    resp.forms[0]['f0'] = 'foobar1'
    resp.forms[0]['f1'] = 'foobar 2'
    resp.forms[0]['f2'] = '2022-01-01'
    resp.forms[0]['f3'] = 'COMPUTED'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()

    # check it ignores case/accent/space differences
    pub.session_manager.session_class.wipe()
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.text.count('Access rights verification') == 2  # <title> and body
    resp.forms[0]['f0'] = 'fööbar1'  # accent
    resp.forms[0]['f1'] = ' foobar  2 '  # spaces
    resp.forms[0]['f2'] = '2022-01-01'
    resp.forms[0]['f3'] = 'computed'  # case
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()

    # check anonymous user can't get to it from the URL
    pub.session_manager.session_class.wipe()
    resp = get_app(pub).get('http://example.net/test/%s/' % formdata_id)
    assert resp.location.startswith('http://example.net/login')
    # or logged users that didn't enter the code:
    create_user(pub)
    login(get_app(pub), username='foo', password='foo').get(
        'http://example.net/test/%s/' % formdata_id, status=403
    )

    # verification failure
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert 'Access rights verification' in resp
    resp.forms[0]['f0'] = 'foobar1'  # ok
    resp.forms[0]['f1'] = 'barfoo2'  # ko
    resp.forms[0]['f2'] = '2022-01-01'  # ok
    resp.forms[0]['f3'] = 'COMPUTED'  # ok
    resp = resp.forms[0].submit('submit')
    assert 'Access denied: this content does not match the form' in resp
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert 'Access rights verification' in resp
    resp.forms[0]['f0'] = 'foobar1'  # ok
    resp.forms[0]['f1'] = 'foobar 2'  # ok
    resp.forms[0]['f2'] = '2022-01-02'  # ko
    resp.forms[0]['f3'] = 'COMPUTED'  # ok
    resp = resp.forms[0].submit('submit')
    assert 'Access denied: this content does not match the form' in resp

    # check it doesn't ignore case/accent/space differences
    pub.site_options.set('options', 'use-strict-check-for-verification-fields', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert 'Access rights verification' in resp
    resp.forms[0]['f0'] = 'fööbar1'  # accent
    resp.forms[0]['f1'] = ' foobar  2 '  # spaces
    resp.forms[0]['f2'] = '2022-01-01'
    resp.forms[0]['f3'] = 'computed'  # case
    resp = resp.forms[0].submit('submit')
    assert 'Access denied: this content does not match the form' in resp

    # draft with an empty field: do not verify it
    formdata.data['0'] = None
    formdata.store()
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert 'Access rights verification' in resp
    assert 'f0' not in resp.forms[0].fields
    assert 'f1' in resp.forms[0].fields
    resp.forms[0]['f1'] = 'foobar 2'
    resp.forms[0]['f2'] = '2022-01-01'
    resp.forms[0]['f3'] = 'COMPUTED'  # ok
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()

    # empty draft: no verification
    formdata.data['1'] = None
    formdata.data['2'] = None
    formdata.data['3'] = None
    formdata.store()
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')

    # not a draft: all validation fields are required
    formdata.status = 'wf-new'
    formdata.data['0'] = 'foobar1'
    formdata.store()
    resp = get_app(pub).get('http://example.net/code/%s/load' % tracking_code)
    assert 'Access rights verification' in resp
    assert 'f0' in resp.forms[0].fields
    assert 'f1' in resp.forms[0].fields
    assert 'f2' in resp.forms[0].fields
    assert 'f3' in resp.forms[0].fields
    resp.forms[0]['f0'] = 'foobar1'
    resp.forms[0]['f1'] = ''
    resp.forms[0]['f2'] = ''
    resp.forms[0]['f3'] = ''
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert 'foobar1' in resp.text
    assert 'form_comment' in resp.text  # user is treated as submitter

    # verification failure
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert 'Access rights verification' in resp
    resp.forms[0]['f0'] = 'foobar1'  # ok
    resp.forms[0]['f1'] = 'not empty'  # ko
    resp.forms[0]['f2'] = ''  # ok
    resp.forms[0]['f3'] = ''  # ok
    resp = resp.forms[0].submit('submit')
    assert 'Access denied: this content does not match the form.' in resp

    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert 'Access rights verification' in resp
    resp.forms[0]['f0'] = 'foobar1'  # ok
    resp.forms[0]['f1'] = ''  # ok
    resp.forms[0]['f2'] = '2022-02-02'  # ko (not empty)
    resp.forms[0]['f3'] = ''  # ok
    resp = resp.forms[0].submit('submit')
    assert 'Access denied: this content does not match the form.' in resp


def test_form_tracking_code_rate_limit(pub, freezer):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'rate-limit', '2/2s')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    # twice
    freezer.move_to('2018-12-01T00:00:00')
    get_app(pub).get('/code/ABC/load', status=404)
    get_app(pub).get('/code/ABC/load', status=404)
    # and out
    get_app(pub).get('/code/ABC/load', status=403)
    get_app(pub).get('/code/ABC/load', status=403)
    # wait two second
    freezer.move_to('2018-12-01T00:00:02')
    # and ok again
    get_app(pub).get('/code/ABC/load', status=404)

    # ditto with POST to /code/
    # twice
    freezer.move_to('2019-12-01T00:00:00')
    get_app(pub).post('/code/load', params={'code': 'ABC'}, status=404)
    get_app(pub).post('/code/load', params={'code': 'ABC'}, status=404)
    # and out
    get_app(pub).post('/code/load', params={'code': 'ABC'}, status=403)
    get_app(pub).post('/code/load', params={'code': 'ABC'}, status=403)
    # wait two second
    freezer.move_to('2019-12-01T00:00:02')
    # and ok again
    get_app(pub).post('/code/load', params={'code': 'ABC'}, status=404)


def test_form_tracking_code_as_user(pub, nocache):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    tracking_code_2 = get_displayed_tracking_code(resp)
    assert tracking_code == tracking_code_2

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].is_draft()
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].data['0'] == 'foobar'
    formdata_id = formdef.data_class().select()[0].id

    # check we can load the formdata as a draft
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()
    resp = resp.forms[1].submit('previous')
    assert resp.forms[1]['f0'].value == 'foobar'

    # check submitted form keeps the tracking code
    resp.forms[1]['f0'] = 'barfoo'
    resp = resp.forms[1].submit('submit')  # -> confirmation page
    resp = resp.forms[1].submit('submit')  # -> done
    resp = resp.follow()
    assert 'barfoo' in resp.text
    assert formdef.data_class().count() == 1  # check the draft one has been removed
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert str(formdef.data_class().select()[0].user_id) == str(user.id)
    assert formdef.data_class().select()[0].status == 'wf-new'
    assert formdef.data_class().select()[0].data['0'] == 'barfoo'
    formdata_id = formdef.data_class().select()[0].id

    # check we can still go back to it
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert 'form_comment' in resp.text  # makes sure user is treated as submitter
    resp.forms[0]['comment'] = 'hello world'
    resp = resp.forms[0].submit()
    assert formdef.data_class().get(formdata_id).evolution[-1].get_plain_text_comment() == 'hello world'

    # and check we can also get back to it as anonymous
    app = get_app(pub)
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert 'form_comment' in resp.text  # makes sure user is treated as submitter

    # and check a bot is not allowed to get it
    app = get_app(pub)
    resp = app.get('/code/%s/load' % tracking_code, headers={'User-agent': 'Googlebot'}, status=403)

    # check we can't get back to it once anonymised
    formdata = formdef.data_class().get(formdata_id)
    formdata.anonymise()

    app = get_app(pub)
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit(status=404)


def test_form_empty_tracking_code(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    # check we get a 404 if we use the tracking code before it gets any data
    app = get_app(pub)
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit(status=404)


def test_form_tracking_code_remove_draft(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].is_draft()
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].data['0'] == 'foobar'
    assert str(formdef.data_class().select()[0].page_no) == '1'
    formdata_id = formdef.data_class().select()[0].id

    app = get_app(pub)

    # visit page, check there's no remove draft button
    resp = app.get('/test/')
    assert '<h3>Tracking code</h3>' in resp.text
    assert 'removedraft' not in resp.text

    # check we can load the formdata as a draft
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()
    assert '<h3>Tracking code</h3>' in resp.text
    assert 'removedraft' in resp.text
    resp = resp.forms[1].submit('previous')
    assert resp.forms[1]['f0'].value == 'foobar'

    resp = resp.forms[0].submit()  # remove draft
    assert resp.location == 'http://example.net/'
    assert formdef.data_class().count() == 0


def test_form_tracking_code_remove_empty_draft(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('previous')
    resp_autosave = app.post('/test/autosave', params=resp.form.submit_fields())
    assert resp_autosave.json == {'result': 'success'}
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].is_draft()
    assert formdef.data_class().select()[0].tracking_code == tracking_code
    assert formdef.data_class().select()[0].data['0'] == 'foobar'
    assert str(formdef.data_class().select()[0].page_no) == '0'

    # make draft empty
    formdata = formdef.data_class().select()[0]
    formdata.data = {}
    formdata.store()
    formdata_id = formdef.data_class().select()[0].id

    app = get_app(pub)

    # check we can load the formdata as a draft
    resp = app.get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata_id
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()
    assert '<h3>Tracking code</h3>' in resp.text
    assert 'removedraft' in resp.text
    assert resp.forms[1]['f0'].value == ''

    resp = resp.forms[0].submit()  # remove draft
    assert resp.location == 'http://example.net/'
    assert formdef.data_class().count() == 0


def test_form_invalid_tracking_code(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()

    # create a secondary formdef, to always have the tracking code form
    # displayed on homepage
    formdef2 = FormDef()
    formdef2.name = 'test2'
    formdef2.fields = []
    formdef2.enable_tracking_codes = True
    formdef2.store()

    resp = get_app(pub).get('/')

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foobar'}
    formdata.store()

    # check we can go back to it
    formdef.data_class().wipe()

    code = TrackingCode()
    code.formdata = formdata  # this will save it again
    code.store()

    resp.forms[0]['code'] = code.id
    resp = resp.forms[0].submit()
    resp = resp.follow()
    assert resp.location == 'http://example.net/test/%s/' % formdata.id
    resp = resp.follow()

    # check we get a not found error message on non-existent code
    fake_code = TrackingCode().get_new_id()
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = fake_code
    resp = resp.forms[0].submit(status=404)

    # check we also get an error if tracking code access is disabled after the
    # fact
    formdef.enable_tracking_codes = False
    formdef.store()
    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = code.id
    resp = resp.forms[0].submit()
    resp = resp.follow(status=404)


def test_form_tracking_code_as_variable(pub, nocache):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(id='3', label='!{{ form_tracking_code }}!'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None
    assert '!%s!' % tracking_code in resp.text


def test_tracking_code_in_url(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')
    tracking_code = get_displayed_tracking_code(resp)
    resp = resp.form.submit('submit')
    formdata = formdef.data_class().select()[0]

    resp = get_app(pub).get(f'/code/{tracking_code}/load')
    assert resp.location == formdata.get_url()

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'allow-tracking-code-in-url', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    get_app(pub).get(f'/code/{tracking_code}/load', status=403)


def test_temporary_access_url(pub, freezer):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foobar'}
    formdata.status = 'draft'
    formdata.store()

    # missing token
    get_app(pub).get(f'/code/{"a"*64}/load', status=404)
    # token of invalid type
    token = pub.token_class(size=64)
    token.type = 'whatever'
    token.store()
    get_app(pub).get(f'/code/{token.id}/load', status=404)
    # token to invalid formdef
    token.type = 'temporary-access-url'
    token.context = {'form_slug': 'xxx'}
    token.store()
    get_app(pub).get(f'/code/{token.id}/load', status=404)
    # token to invalid formdata
    token.context = {'form_slug': formdef.slug, 'form_number_raw': 123}
    token.store()
    get_app(pub).get(f'/code/{token.id}/load', status=404)
    # valid token
    token.context = {'form_slug': formdef.slug, 'form_number_raw': formdata.id}
    token.store()
    resp = get_app(pub).get(f'/code/{token.id}/load', status=302)
    assert resp.location == formdata.get_url()

    # using dedicated method
    resp = get_app(pub).get(formdata.get_temporary_access_url(5)).follow().follow()
    assert resp.forms[1]['f0'].value == 'foobar'

    # bypass check
    formdef.enable_tracking_codes = False
    formdef.store()
    get_app(pub).get(formdata.get_temporary_access_url(5), status=404)
    get_app(pub).get(formdata.get_temporary_access_url(5, bypass_checks=True), status=302)

    # verification fields and bypass
    formdef.enable_tracking_codes = True
    formdef.tracking_code_verify_fields = ['0']
    formdef.store()
    resp = get_app(pub).get(formdata.get_temporary_access_url(5))
    assert 'Access rights verification' in resp.text
    get_app(pub).get(formdata.get_temporary_access_url(5, bypass_checks=True), status=302)

    # check template tag
    pub.substitutions.reset()
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables()
    tmpl = Template('{% temporary_access_url %}')
    resp = get_app(pub).get(tmpl.render(context))
    assert 'Access rights verification' in resp.text
    tmpl = Template('{% temporary_access_url bypass_checks=True %}')
    resp = get_app(pub).get(tmpl.render(context), status=302)

    # check duration calculation
    pub.token_class.wipe()
    freezer.move_to(make_aware(datetime.datetime(2023, 10, 7, 10, 0, 0)))
    tmpl = Template('{% temporary_access_url days=1 hours=1 minutes=2 seconds=3 %}')
    tmpl.render(context)
    assert pub.token_class.select()[0].expiration.timetuple()[:6] == (2023, 10, 8, 11, 2, 3)

    # check default duration
    pub.token_class.wipe()
    freezer.move_to(make_aware(datetime.datetime(2023, 10, 7, 10, 0, 0)))
    tmpl = Template('{% temporary_access_url %}')
    tmpl.render(context)
    assert pub.token_class.select()[0].expiration.timetuple()[:6] == (2023, 10, 7, 10, 30, 0)

    # check max duration
    pub.token_class.wipe()
    freezer.move_to(make_aware(datetime.datetime(2023, 10, 7, 10, 0, 0)))
    tmpl = Template('{% temporary_access_url days=100 %}')
    tmpl.render(context)
    assert pub.token_class.select()[0].expiration.timetuple()[:6] == (2023, 10, 17, 10, 0, 0)

    # check there's no url generated for carddata objects
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='string')]
    carddef.enable_tracking_codes = True
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foobar'}
    carddata.status = 'draft'
    carddata.store()

    pub.substitutions.reset()
    pub.substitutions.feed(carddef)
    pub.substitutions.feed(carddata)
    context = pub.substitutions.get_context_variables()
    tmpl = Template('{% temporary_access_url %}')
    assert tmpl.render(context) == ''


def test_form_tracking_code_workflow_action(pub):
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> done
    formdata = formdef.data_class().select()[0]

    resp = get_app(pub).get(formdata.get_url(), status=302)  # redirection to login

    resp = get_app(pub).get('/')
    resp.forms[0]['code'] = formdata.tracking_code
    resp = resp.forms[0].submit().follow().follow()
    resp.forms['wf-actions']['comment'] = 'Test comment'
    resp = resp.forms['wf-actions'].submit('button_commentable')

    # check action has been recorded as submitter
    formdata.refresh_from_storage()
    assert isinstance(formdata.evolution[-1].parts[0], WorkflowCommentPart)
    assert formdata.evolution[-1].who == '_submitter'
