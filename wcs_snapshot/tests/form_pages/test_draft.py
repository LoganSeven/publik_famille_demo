import datetime
import time
from unittest import mock

import pytest
from django.utils.timezone import make_aware
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.formdef import FormDef
from wcs.qommon.storage import NothingToUpdate
from wcs.sql_criterias import Equal

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_formdef, create_user, get_displayed_tracking_code


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub(lazy_mode=bool('lazy' in request.param))
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_form_discard_draft(pub, nocache):
    create_user(pub)

    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = False
    formdef.store()
    formdef.data_class().wipe()

    # anonymous user, no tracking code (-> no draft)
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == []
    assert 'Cancel' in resp.text
    assert 'Discard' not in resp.text
    resp = resp.form.submit('cancel')

    # anonymous user, tracking code (-> draft)
    formdef.enable_tracking_codes = True
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == ['draft']
    assert 'Cancel' not in resp.text
    assert 'Discard' in resp.text
    resp = resp.form.submit('cancel')
    assert [x.status for x in formdef.data_class().select()] == []  # discarded

    # logged-in user, no tracking code
    formdef.enable_tracking_codes = False
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == ['draft']
    assert 'Cancel' not in resp.text
    assert 'Discard' in resp.text
    resp = resp.form.submit('cancel')
    assert [x.status for x in formdef.data_class().select()] == []  # discarded

    # logged-in user, tracking code
    formdef.enable_tracking_codes = True
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == ['draft']
    assert 'Cancel' not in resp.text
    assert 'Discard' in resp.text
    resp = resp.form.submit('cancel')
    assert [x.status for x in formdef.data_class().select()] == []  # discarded

    # anonymous user, tracking code, recalled
    formdef.enable_tracking_codes = True
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == ['draft']
    assert 'Cancel' not in resp.text
    assert 'Discard' in resp.text
    tracking_code = get_displayed_tracking_code(resp)

    resp = get_app(pub).get('/')
    resp.form['code'] = tracking_code
    resp = resp.form.submit().follow().follow().follow()
    assert resp.forms[1]['f0'].value == 'foobar'
    assert 'Cancel' in resp.text
    assert 'Discard Draft' in resp.text
    resp = resp.forms[1].submit('cancel')
    assert [x.status for x in formdef.data_class().select()] == ['draft']

    # logged-in user, no tracking code, recalled
    formdef.data_class().wipe()
    formdef.enable_tracking_codes = False
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'foobar'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert [x.status for x in formdef.data_class().select()] == ['draft']
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.click('Continue with draft').follow()
    assert 'Cancel' in resp.text
    assert 'Discard Draft' in resp.text
    resp = resp.forms[1].submit('cancel')
    assert [x.status for x in formdef.data_class().select()] == ['draft']


def test_form_invalid_previous_data(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.DateField(id='0', label='date')]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None
    resp.forms[0]['f0'] = time.strftime('%Y-%m-%d', time.localtime())
    resp = resp.forms[0].submit('submit')  # -> validation page

    formdef.fields[0].minimum_is_future = True
    formdef.store()

    # load the formdata as a draft
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit().follow().follow().follow()
    assert resp.forms[1]['f0'].value == time.strftime('%Y-%m-%d', time.localtime())
    resp = resp.forms[1].submit('submit')  # -> submit
    assert 'This form has already been submitted.' not in resp.text
    assert 'Unexpected field error' in resp.text


def test_form_draft_with_file(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    assert '<h3>Tracking code</h3>' in resp.text
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None
    resp.forms[0]['f0$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('submit')
    tracking_code_2 = get_displayed_tracking_code(resp)
    assert tracking_code == tracking_code_2

    # check we can load the formdata as a draft
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit()
    resp = resp.follow()
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/test/?mt=')
    resp = resp.follow()
    resp = resp.forms[1].submit('previous')
    assert resp.pyquery('.filename').text() == 'test.txt'
    # check file is downloadable
    r2 = resp.click('test.txt')
    assert r2.content_type == 'text/plain'
    assert r2.text == 'foobar'

    # check submitted form keeps the file
    resp = resp.forms[1].submit('submit')  # -> confirmation page
    resp = resp.forms[1].submit('submit')  # -> done
    resp = resp.follow()

    assert resp.click('test.txt').follow().text == 'foobar'


def test_form_draft_with_file_direct_validation(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    tracking_code = get_displayed_tracking_code(resp)
    resp.forms[0]['f0$file'] = Upload('test2.txt', b'foobar2', 'text/plain')
    resp = resp.forms[0].submit('submit')

    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit().follow().follow().follow()
    assert 'test2.txt' in resp.text

    # check submitted form keeps the file
    resp = resp.forms[1].submit('submit')  # -> done
    resp = resp.follow()

    assert resp.click('test2.txt').follow().text == 'foobar2'


def test_form_draft_with_date(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.DateField(id='0', label='date')]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    tracking_code = get_displayed_tracking_code(resp)
    resp.forms[0]['f0'] = '2012-02-12'
    resp = resp.forms[0].submit('submit')

    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit().follow().follow().follow()
    assert '2012-02-12' in resp.text

    # check submitted form keeps the date
    resp = resp.forms[1].submit('submit')  # -> done
    resp = resp.follow()

    assert '2012-02-12' in resp.text


def test_form_draft_save_on_error_page(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.fields = [
        fields.StringField(id='1', label='string1', required='optional'),
        fields.StringField(id='2', label='string2', required='required'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    formdef.data_class().wipe()
    tracking_code = get_displayed_tracking_code(resp)
    resp.forms[0]['f1'] = 'plop'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('#form_error_f2').text() == 'required field'

    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp.forms[0]['code'] = tracking_code
    resp = resp.forms[0].submit().follow().follow().follow()
    assert resp.forms[1]['f1'].value == 'plop'


@pytest.mark.parametrize('tracking_code', [True, False])
def test_form_direct_draft_access(pub, tracking_code):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.enable_tracking_codes = tracking_code
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foobar'}
    formdata.status = 'draft'
    formdata.store()

    resp = get_app(pub).get('/test/%s/' % formdata.id, status=302)
    assert resp.location.startswith('http://example.net/login')

    formdata.user_id = user.id
    formdata.store()
    resp = get_app(pub).get('/test/%s/' % formdata.id, status=302)
    assert resp.location.startswith('http://example.net/login')

    resp = login(get_app(pub), 'foo', 'foo').get('/test/%s/' % formdata.id, status=302)
    assert resp.location.startswith('http://example.net/test/?mt=')

    formdata.user_id = 1000
    formdata.store()
    resp = login(get_app(pub), 'foo', 'foo').get('/test/%s/' % formdata.id, status=403)


def test_form_new_table_rows_field_draft_recall(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    tracking_code = get_displayed_tracking_code(resp)
    assert tracking_code is not None

    # add new table rows field to formdef
    formdef.fields.append(
        fields.TableRowsField(id='3', label='table', columns=['a', 'b'], required='optional')
    )
    formdef.store()

    # restore form on validation page
    resp = get_app(pub).get('/')
    resp.form['code'] = tracking_code
    resp = resp.form.submit().follow().follow().follow()

    # validate form
    resp = resp.forms[1].submit()
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].data['1'] == 'test'
    assert formdef.data_class().select()[0].data['3'] is None


def test_form_recall_draft(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert 'You already started to fill this form.' not in resp.text

    draft = formdef.data_class()()
    draft.user_id = user.id
    draft.status = 'draft'
    draft.data = {}
    draft.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert 'You already started to fill this form.' in resp.text
    assert 'href="%s/"' % draft.id in resp.text

    draft2 = formdef.data_class()()
    draft2.user_id = user.id
    draft2.status = 'draft'
    draft2.data = {}
    draft2.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert 'You already started to fill this form.' in resp.text
    assert 'href="%s/"' % draft.id in resp.text
    assert 'href="%s/"' % draft2.id in resp.text


def test_form_recall_draft_digests(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string', varname='name')]
    formdef.digest_templates = {'default': 'digest{{form_var_name}}digest'}
    formdef.store()
    formdef.data_class().wipe()

    draft = formdef.data_class()()
    draft.user_id = user.id
    draft.status = 'draft'
    draft.data = {'0': 'DIGEST'}
    draft.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    # single draft, digest is not displayed
    assert 'digestDIGESTdigest' not in resp.pyquery(f'[href="{draft.id}/"]').text()

    draft2 = formdef.data_class()()
    draft2.user_id = user.id
    draft2.status = 'draft'
    draft2.data = {}
    draft2.store()

    resp = app.get('/test/')
    # two drafts, the first one has its digest displayed
    assert 'digestDIGESTdigest' in resp.pyquery(f'[href="{draft.id}/"]').text()
    # the second doesn't have it as it contains "None"
    assert (
        resp.pyquery(f'[href="{draft2.id}/"]').text()
        and draft2.default_digest not in resp.pyquery(f'[href="{draft2.id}/"]').text()
    )


def test_form_max_drafts(pub):
    user = create_user(pub)

    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.store()
    formdef.data_class().wipe()

    # create another draft, not linked to user, to check it's not deleted
    another_draft = formdef.data_class()()
    another_draft.status = 'draft'
    another_draft.receipt_time = make_aware(datetime.datetime(2023, 11, 23, 0, 0))
    another_draft.store()

    drafts = []
    for i in range(4):
        draft = formdef.data_class()()
        draft.user_id = user.id
        draft.status = 'draft'
        draft.receipt_time = make_aware(datetime.datetime(2023, 11, 23, 0, i))
        draft.store()
        drafts.append(draft)

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert resp.pyquery('.drafts-recall a').length == 4
    resp.form['f0'] = 'hello'
    resp = resp.form.submit('submit')
    assert formdef.data_class().count([Equal('status', 'draft')]) == 6

    resp = app.get('/test/')
    assert resp.pyquery('.drafts-recall a').length == 5
    resp.form['f0'] = 'hello2'
    resp = resp.form.submit('submit')
    assert formdef.data_class().count([Equal('status', 'draft')]) == 6

    assert not formdef.data_class().has_key(drafts[0].id)  # oldest draft was removed

    formdef.drafts_max_per_user = '3'
    formdef.store()

    resp = app.get('/test/')
    resp.form['f0'] = 'hello2'
    resp = resp.form.submit('submit')
    assert formdef.data_class().count([Equal('status', 'draft')]) == 4


def test_form_draft_temporary_access_url(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.enable_tracking_codes = True
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(
            id='3', label='<a href="{% temporary_access_url bypass_checks=True %}">label</a>'
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'] = 'foo'
    resp = resp.forms[0].submit('submit')  # next page
    assert '/code/' in resp.pyquery('.comment-field a').attr.href
    resp = resp.click('label').follow().follow()
    resp = resp.forms[1].submit('previous')
    assert resp.forms[1]['f1'].value == 'foo'


def test_form_previous_on_submitted_draft(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
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
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'foobar2'
    resp = resp.form.submit('submit')  # -> validation
    resp.form.submit('submit').follow()  # -> submit

    # simulate the user going back and then clicking on previous
    resp = resp.form.submit('previous').follow()
    assert 'This form has already been submitted.' in resp.text

    # again but simulate browser stuck on the validation page while the form
    # is being recorded and the magictoken not yet being removed when the user
    # clicks the "previous page" button
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'foobar2'
    resp = resp.form.submit('submit')  # -> validation

    with mock.patch('wcs.sql.Session.remove_magictoken') as remove_magictoken:
        resp.form.submit('submit').follow()  # -> submit
        assert remove_magictoken.call_count == 1

    resp = resp.form.submit('previous').follow()  # -> page 2
    assert 'This form has already been submitted.' in resp.text


def test_form_add_row_on_submitted_draft(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test1')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='block', block_slug='foobar', max_items='3'),
    ]
    formdef.enable_tracking_codes = True
    formdef.confirmation = False
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)

    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3$element0$f123'] = 'foo'

    with mock.patch('wcs.sql.Session.remove_magictoken') as remove_magictoken:
        resp.form.submit('submit').follow()  # -> submit
        assert remove_magictoken.call_count == 1

    # simulate the user going back and then clicking on "add block row" button
    resp = resp.form.submit('f3$add_element').follow()
    assert 'This form has already been submitted.' in resp.text


def test_nothing_to_update_add_row(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test1')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.BlockField(id='2', label='block', block_slug='foobar', max_items='3'),
    ]
    formdef.enable_tracking_codes = True
    formdef.confirmation = True
    formdef.store()

    formdef.data_class().wipe()
    app = get_app(pub)

    resp = app.get('/test/')
    resp.form['f2$element0$f123'] = 'foo'

    with mock.patch('wcs.sql.SqlDataMixin.store') as sql_data_store:
        sql_data_store.side_effect = NothingToUpdate
        resp = resp.form.submit('f2$add_element').follow()
    assert 'Technical error saving draft, please try again.' in resp.text


def test_draft_with_block_data(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test1')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='block', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    formdef.data_class().wipe()

    create_user(pub)
    app = get_app(pub)
    resp = login(app, username='foo', password='foo').get('/test/')

    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> confirmation page

    resp = app.get('/test/')
    resp = resp.click('Continue with draft').follow()
    assert resp.forms[1]['f3$element0$f123'].value == 'foo'
    resp = resp.forms[1].submit('previous')  # -> page 2
    assert resp.forms[1]['f3$element0$f123'].value == 'foo'
    resp = resp.forms[1].submit('submit')  # -> confirmation page
    resp = resp.forms[1].submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '3': {'data': [{'123': 'foo'}], 'schema': {'123': 'string'}},
        '3_display': 'foobar',
    }


def test_draft_with_block_data_tracking_code(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test1')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='block', block_slug='foobar', max_items='3'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> confirmation page
    tracking_code = get_displayed_tracking_code(resp)

    resp = get_app(pub).get('/')
    resp.form['code'] = tracking_code
    resp = resp.form.submit().follow().follow().follow()
    assert resp.forms[1]['f3$element0$f123'].value == 'foo'
    resp = resp.forms[1].submit('previous')  # -> page 2
    assert resp.forms[1]['f3$element0$f123'].value == 'foo'
    resp = resp.forms[1].submit('submit')  # -> confirmation page
    resp = resp.forms[1].submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '3': {'data': [{'123': 'foo'}], 'schema': {'123': 'string'}},
        '3_display': 'foobar',
    }


def test_draft_store_page_id(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3'),
    ]
    formdef.store()
    first_page_id = formdef.fields[0].id
    second_page_id = formdef.fields[2].id
    third_page_id = formdef.fields[4].id

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f1'] = 'test'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == first_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] is None
    assert formdata.data['5'] is None

    resp = resp.form.submit('submit')
    # first page submitted, the draft in on the seconde page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == second_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] is None
    assert formdata.data['5'] is None

    resp.form['f3'] = 'foo'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == second_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] is None

    resp = resp.form.submit('submit')
    # second page submitted, the draft in on the third page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '2'
    assert formdata.page_id == third_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] is None

    resp.form['f5'] = 'bar'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '2'
    assert formdata.page_id == third_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] == 'bar'

    resp = resp.form.submit('submit')
    # third page submitted, the draft in on the confirmation page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '3'
    assert formdata.page_id == '_confirmation_page'
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] == 'bar'

    resp = resp.form.submit('previous')
    # back to third page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '2'
    assert formdata.page_id == third_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] == 'bar'


def test_draft_store_page_id_no_confirmation(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3'),
    ]
    formdef.confirmation = False
    formdef.store()
    first_page_id = formdef.fields[0].id
    second_page_id = formdef.fields[2].id
    third_page_id = formdef.fields[4].id

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f1'] = 'test'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == first_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] is None
    assert formdata.data['5'] is None

    resp = resp.form.submit('submit')
    # first page submitted, the draft in on the seconde page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == second_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] is None
    assert formdata.data['5'] is None

    resp.form['f3'] = 'foo'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == second_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] is None

    resp = resp.form.submit('submit')
    # second page submitted, the draft in on the third page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '2'
    assert formdata.page_id == third_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] is None

    resp.form['f5'] = 'bar'
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '2'
    assert formdata.page_id == third_page_id
    assert formdata.data['1'] == 'test'
    assert formdata.data['3'] == 'foo'
    assert formdata.data['5'] == 'bar'

    resp = resp.form.submit('submit')
    # third page submitted, no more draft
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-new'


def test_draft_store_page_id_when_no_page(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.StringField(id='1', label='string 1'),
        fields.StringField(id='2', label='string 2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f1'] = 'test'
    resp.form['f2'] = 'bar'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == '_first_page'
    assert formdata.data['1'] == 'test'
    assert formdata.data['2'] == 'bar'

    resp = resp.form.submit('submit')
    # fields submitted, the draft in on the confirmation page
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == '_confirmation_page'
    assert formdata.data['1'] == 'test'

    # back to first page
    resp = resp.form.submit('previous')
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == '_first_page'
    assert formdata.data['1'] == 'test'


def test_draft_store_page_id_when_no_page_and_no_confirmation(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.StringField(id='1', label='string 1'),
        fields.StringField(id='2', label='string 2'),
    ]
    formdef.confirmation = False
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f1'] = 'test'
    resp.form['f2'] = 'bar'
    # autosave
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == '_first_page'
    assert formdata.data['1'] == 'test'
    assert formdata.data['2'] == 'bar'

    resp = resp.form.submit('submit')
    # fields submitted, no more draft
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-new'


def test_draft_error_then_autosave(pub):
    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    resp = resp.form.submit('submit')  # error
    assert formdef.data_class().count() == 1  # server roundtrip -> draft

    resp.form['f1'] = 'test'
    app.post('/test/autosave', params=resp.form.submit_fields())
    assert formdef.data_class().count() == 1  # make sure same draft got reused
    assert formdef.data_class().select()[0].data['1'] == 'test'
