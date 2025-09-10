import datetime
import os
import re
from unittest import mock

import pytest
import responses
from django.utils.timezone import localtime, make_aware
from pyquery import PyQuery

from wcs import fields
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.tracking_code import TrackingCode
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser, create_user


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


@pytest.fixture
def autosave(pub):
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'backoffice-autosave', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_submission(pub):
    user = create_user(pub)

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.ItemField(
            id='2',
            label='2nd field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.ItemField(id='3', label='3rd field', data_source=datasource, varname='foo'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-submission')
    app.get('/backoffice/submission/', status=403)

    formdef = FormDef.get_by_urlname('form-title')
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-submission')
    resp = app.get('/backoffice/submission/').follow()
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    assert resp.pyquery('form[data-autosave=false]').length
    resp.form['f1'] = 'test submission'
    resp.form['f2'] = 'baz'
    resp.form['f3'] = 'C'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    # going back to first page, to check
    resp = resp.form.submit('previous')
    assert resp.form['f1'].value == 'test submission'
    resp = resp.form.submit('submit')

    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).data['1'] == 'test submission'
    assert data_class.get(formdata_no).data['2'] == 'baz'
    assert data_class.get(formdata_no).status == 'wf-new'
    assert data_class.get(formdata_no).user is None
    assert data_class.get(formdata_no).backoffice_submission is True

    resp = resp.follow()  # get to the formdata page

    formdata_count = data_class.count()

    # test submission when agent is not receiver
    formdef.workflow_roles = {}
    formdef.store()
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp.form['f2'] = 'baz'
    resp.form['f3'] = 'C'
    resp = resp.form.submit('submit')  # to validation screen
    resp = resp.form.submit('submit')  # final submit
    assert resp.location == 'http://example.net/backoffice/submission/'
    resp = resp.follow()  # should go back to submission screen

    assert data_class.count() == formdata_count + 1

    # test redirection on cancel
    resp = app.get('/backoffice/submission/new')
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/submission/'

    # test submission when agent is not receiver but there's a redirect action
    # in the workflow.
    formdef = FormDef.get_by_urlname('form-title')
    wf = Workflow(name='dispatch')
    st1 = wf.add_status('Status1')
    item = st1.add_action('redirect_to_url', id='_redirect')
    item.url = 'http://www.example.org/'
    wf.store()
    formdef.workflow_id = wf.id
    formdef.store()

    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp.form['f2'] = 'baz'
    resp.form['f3'] = 'C'
    resp = resp.form.submit('submit')  # to validation screen
    resp = resp.form.submit('submit')  # final submit
    assert resp.location == 'http://www.example.org/'


def test_backoffice_submission_menu_entry(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-submission')

    pub.cfg['backoffice-submission'] = {}
    pub.cfg['backoffice-submission']['sidebar_menu_entry'] = 'visible'
    pub.write_cfg()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-submission')

    pub.cfg['backoffice-submission']['sidebar_menu_entry'] = 'redirect'
    pub.cfg['backoffice-submission']['redirect'] = 'https://example.net/'
    pub.write_cfg()
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-submission')
    resp = app.get('/backoffice/submission/', status=302)
    assert resp.location == 'https://example.net/'

    pub.cfg['backoffice-submission']['sidebar_menu_entry'] = 'hidden'
    pub.write_cfg()
    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-submission')
    resp = app.get('/backoffice/submission/', status=302)
    assert resp.location == 'https://example.net/'

    pub.cfg['backoffice-submission'][
        'redirect'
    ] = '{% if session_user_email == "admin@localhost" %}https://example.net/{% endif %}'
    pub.write_cfg()
    assert app.get('/backoffice/submission/', status=302).location == 'https://example.net/'
    user.email = 'admin2@localhost'
    user.store()
    # native screen
    assert (
        app.get('/backoffice/submission/', status=302).location
        == 'http://example.net/backoffice/submission/new'
    )

    pub.cfg['backoffice-submission']['redirect'] = 'https://example.net/'
    pub.cfg['backoffice-submission']['default_screen'] = 'new'
    pub.write_cfg()
    assert (
        app.get('/backoffice/submission/', status=302).location
        == 'http://example.net/backoffice/submission/new'
    )

    pub.cfg['backoffice-submission']['redirect'] = 'https://example.net/'
    pub.cfg['backoffice-submission']['default_screen'] = 'pending'
    pub.write_cfg()
    assert (
        app.get('/backoffice/submission/', status=302).location
        == 'http://example.net/backoffice/submission/pending'
    )

    pub.cfg['backoffice-submission']['redirect'] = 'https://example.net/'
    pub.cfg['backoffice-submission']['default_screen'] = 'custom'
    pub.write_cfg()
    assert app.get('/backoffice/submission/', status=302).location == 'https://example.net/'


def test_backoffice_submission_with_tracking_code(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    # final submit
    validation_resp_body = resp.text
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    formdata = data_class.get(formdata_no)
    assert formdata.tracking_code in validation_resp_body

    formdata_location = resp.location
    resp = resp.follow()  # get to the formdata page
    # check tracking code is still displayed in formdata page
    assert 'test submission' in resp.text
    assert formdata.tracking_code in resp.text

    # check access by different user
    formdata.submission_agent_id = '10000'
    formdata.store()
    resp = app.get(formdata_location)
    assert 'test submission' in resp.text
    assert formdata.tracking_code not in resp.text

    # restore user
    formdata.submission_agent_id = str(user.id)
    formdata.store()
    resp = app.get(formdata_location)
    assert formdata.tracking_code in resp.text

    # check access at a later time
    formdata.receipt_time = localtime() - datetime.timedelta(hours=1)
    formdata.store()
    resp = app.get(formdata_location)
    assert formdata.tracking_code not in resp.text


def test_backoffice_submission_with_return_url(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/?ReturnURL=https://example.org')
    resp = resp.follow().follow()
    resp = resp.form.submit('cancel')
    assert resp.location == 'https://example.org'

    resp = app.get('/backoffice/submission/form-title/?ReturnURL=https://example.org')
    resp = resp.follow().follow()
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> to validation
    resp = resp.form.submit('submit')  # -> to submit
    assert resp.location.startswith('http://example.net/backoffice/management/form-title/')

    # test submission when agent is not receiver
    formdef.workflow_roles = {}
    formdef.store()
    resp = app.get('/backoffice/submission/form-title/?ReturnURL=https://example.org')
    resp = resp.follow().follow()
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> to validation
    resp = resp.form.submit('submit')  # -> to submit
    assert resp.location == 'https://example.org'

    # test removal of draft
    resp = app.get('/backoffice/submission/form-title/?ReturnURL=https://example.org')
    resp = resp.follow().follow()
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> to validation
    resp = resp.click('Discard this form')
    resp = resp.form.submit('discard')
    assert resp.location == 'https://example.org'


def test_backoffice_submission_with_cancel_url(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/?cancelurl=https://example.org')
    resp = resp.follow().follow()
    resp = resp.form.submit('cancel')
    assert resp.location == 'https://example.org'

    # check cancelurl is used, not return url
    resp = app.get(
        '/backoffice/submission/form-title/?cancelurl=https://example.org&ReturnURL=https://example.com'
    )
    resp = resp.follow().follow()
    resp = resp.form.submit('cancel')
    assert resp.location == 'https://example.org'

    # test submission when agent is not receiver (cancelurl should not be used in this case)
    formdef.workflow_roles = {}
    formdef.store()
    resp = app.get('/backoffice/submission/form-title/?cancelurl=https://example.org')
    resp = resp.follow().follow()
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> to validation
    resp = resp.form.submit('submit')  # -> to submit
    assert resp.location == 'http://example.net/backoffice/submission/'

    # test removal of draft
    resp = app.get('/backoffice/submission/form-title/?cancelurl=https://example.org')
    resp = resp.follow().follow()
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> to validation
    resp = resp.click('Discard this form')
    resp = resp.form.submit('discard')
    assert resp.location == 'https://example.org'


def test_backoffice_submission_with_caller(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/?caller=0601020304')
    resp = resp.follow().follow()
    assert '<h3>Phone: 0601020304</h3>' not in resp.text
    assert formdef.data_class().get(1).submission_channel == ''
    assert formdef.data_class().get(1).submission_context == {'submission-locked': {}}

    resp = app.get('/backoffice/submission/form-title/?channel=phone&caller=0601020304')
    resp = resp.follow().follow()
    assert '<h3>Phone: 0601020304</h3>' in resp.text
    assert 'submit-user-selection' in resp
    assert formdef.data_class().get(2).submission_channel == 'phone'
    assert formdef.data_class().get(2).submission_context == {
        'caller': '0601020304',
        'submission-locked': {'channel': 'phone'},
    }

    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp
    resp = resp.form.submit('submit').follow()
    assert formdef.data_class().get(2).submission_channel == 'phone'
    assert formdef.data_class().get(2).submission_context == {'caller': '0601020304'}


def test_backoffice_submission_early_variable(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(
            id='1', label='intro', condition={'type': 'django', 'value': 'not form_submission_backoffice'}
        ),
        fields.PageField(id='2', label='real page'),
        fields.StringField(id='3', label='1st field'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    assert resp.pyquery('#steps .current .wcs-step--label-text').text() == 'real page'


def test_backoffice_parallel_submission(pub, autosave):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.status = 'draft'
    formdata.backoffice_submission = True
    formdata.submission_agent_id = str(user.id)
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/pending')
    assert resp.pyquery('tbody tr')
    resp1 = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp1 = resp1.follow()
    resp2 = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp2 = resp2.follow()
    resp3 = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp3 = resp3.follow()

    assert not resp.pyquery('form[data-autosave=false]').length
    resp1.form['f1'] = 'foo'
    resp1 = resp1.form.submit('submit')  # to validation page

    # also move the second form to the validation page
    resp2.form['f1'] = 'bar'
    resp2 = resp2.form.submit('submit')  # to validation page

    resp1 = resp1.form.submit('submit')  # final validation
    resp1 = resp1.follow()

    resp2 = resp2.form.submit('submit')  # final validation
    assert resp2.status_code == 302
    resp2 = resp2.follow().follow()
    assert 'This form has already been submitted.' in resp2.text

    # do the third form from the start
    resp3.form['f1'] = 'baz'

    resp_autosave = app.post('/backoffice/submission/form-title/autosave', params=resp3.form.submit_fields())
    assert resp_autosave.json['result'] == 'error'
    assert resp_autosave.json['reason'] == 'form has already been submitted'

    resp3 = resp3.form.submit('submit')  # to validation page
    assert resp3.status_code == 302
    resp3 = resp3.follow().follow()
    assert 'This form has already been submitted.' in resp3.text

    assert formdef.data_class().get(formdata.id).data['1'] == 'foo'

    # try again, very late.
    resp4 = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp4 = resp4.follow().follow()
    assert 'This form has already been submitted.' in resp4.text


def test_backoffice_submission_autosave_tracking_code(pub, autosave):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.enable_tracking_codes = True
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foo'

    resp_autosave = app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
    assert resp_autosave.json['result'] == 'success'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> done


def test_backoffice_submission_dispatch(pub):
    user = create_user(pub)

    Workflow.wipe()
    wf = Workflow(name='dispatch')
    st1 = wf.add_status('Status1')
    dispatch = st1.add_action('dispatch', id='_dispatch')
    dispatch.role_key = '_receiver'
    dispatch.role_id = '2'
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # to validation screen
    resp = resp.form.submit('submit')  # final submit
    # should go to the formdata because the formdef is defined as is
    assert resp.location.startswith('http://example.net/backoffice/management/form-title/')

    # remove function from formdef
    formdef.workflow_roles = {}
    formdef.store()

    resp = app.get('/backoffice/submission/new')

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # to validation screen
    resp = resp.form.submit('submit')  # final submit
    # should NOT go to the formdata
    assert resp.location == 'http://example.net/backoffice/submission/'

    # if there's no function but the dispatch sets the right function, should
    # go to the formdata screen
    dispatch.role_id = '1'
    wf.store()

    resp = app.get('/backoffice/submission/new')

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # to validation screen
    resp = resp.form.submit('submit')  # final submit
    # should go to the formdata because the formdata was dispatched to the
    # right role
    assert resp.location.startswith('http://example.net/backoffice/management/form-title/')


def test_backoffice_submission_tracking_code(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    # stop here, don't validate, let user finish it.
    assert data_class.count() == 1
    formdata_no = data_class.select()[0].id

    assert data_class.get(formdata_no).data['1'] == 'test submission'
    assert data_class.get(formdata_no).status == 'draft'
    assert data_class.get(formdata_no).user is None

    resp = get_app(pub).get('/code/%s/load' % data_class.select()[0].tracking_code)
    resp = resp.follow()
    assert resp.location.startswith('http://example.net/form-title/?mt=')
    resp = resp.follow()
    assert 'Check values then click submit.' in resp.text
    assert 'test submission' in resp.text


def test_backoffice_submission_drafts(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.enable_tracking_codes = True
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    assert data_class.count() == 1
    formdata = data_class.select()[0]
    formdata_no = formdata.id
    tracking_code = data_class.select()[0].tracking_code

    # stop here, go back to index
    pub.cfg['submission-channels'] = {'include-in-global-listing': True}
    pub.write_cfg()
    resp = app.get('/backoffice/submission/new')
    resp = resp.click('Pending submissions')
    assert resp.pyquery('tbody tr a').text() == formdata.get_display_name()
    assert resp.pyquery('tbody tr a')[0].attrib['href'] == f'{formdef.url_name}/{formdata_no}/'
    formdata.submission_channel = 'mail'
    formdata.store()
    resp = app.get('/backoffice/submission/pending')
    assert resp.pyquery('tbody td:nth-child(1)').text() == 'Mail'

    # check it can also be accessed using its final URL
    resp2 = app.get('/backoffice/management/%s/%s/' % (formdef.url_name, formdata_no))
    assert resp2.location == 'http://example.net/backoffice/submission/%s/%s/' % (
        formdef.url_name,
        formdata_no,
    )

    resp = resp.click('#%s' % formdata_no)
    resp = resp.follow()
    assert tracking_code in resp.text
    resp = resp.form.submit('previous')
    assert resp.form['f1'].value == 'test submission'

    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    # check it kept the same id
    assert resp.location == 'http://example.net/backoffice/management/form-title/%s/' % formdata_no


def test_backoffice_draft_with_digest(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field', varname='foo'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.digest_templates = {'default': 'digest: {{ form_var_foo }}'}
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'bar'}
    formdata.status = 'draft'
    formdata.backoffice_submission = True
    formdata.submission_agent_id = str(user.id)
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/pending')
    assert resp.pyquery('tbody td:nth-child(1)').text() == 'form title #1-1 digest: bar'


def test_backoffice_submission_remove_drafts(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    TrackingCode.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    assert data_class.count() == 1
    formdata = data_class.select()[0]
    formdata_no = formdata.id

    # stop here, go back to the index
    resp = app.get('/backoffice/submission/pending')
    resp = resp.click('#%s' % formdata_no)
    resp = resp.follow()

    # and try to delete the form (but cancel)
    resp = resp.click('Discard this form')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/submission/'
    assert data_class.count() == 1
    assert TrackingCode.count() == 1

    # and this time for real
    resp = app.get('/backoffice/submission/pending')
    resp = resp.click('#%s' % formdata_no)
    resp = resp.follow()
    resp = resp.click('Discard this form')
    resp = resp.form.submit('discard')
    assert resp.location == 'http://example.net/backoffice/submission/'
    assert data_class.count() == 0
    assert TrackingCode.count() == 0

    # check it's not possible to delete an actual formdata
    formdata = data_class()
    formdata.store()
    resp = app.get('/backoffice/submission/form-title/remove/%s' % formdata.id, status=403)


def test_backoffice_submission_drafts_store_page_id(pub, autosave):
    user = create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string 1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
        fields.PageField(id='4', label='3rd page'),
        fields.StringField(id='5', label='string 3'),
    ]

    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.enable_tracking_codes = True
    formdef.store()
    first_page_id = formdef.fields[0].id
    second_page_id = formdef.fields[2].id
    third_page_id = formdef.fields[4].id
    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test'
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
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
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
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
    # autosave
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
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


def test_backoffice_submission_drafts_store_page_id_when_no_page(pub, autosave):
    user = create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='string 1'),
        fields.StringField(id='2', label='string 2'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.enable_tracking_codes = True
    formdef.store()
    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['f1'] = 'test'
    resp.form['f2'] = 'bar'

    # autosave
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
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


def test_backoffice_submission_live_condition(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar == "bye"'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1'] = 'hello'
    live_url = resp.html.find('form').attrs['data-live-url']
    assert '/backoffice/' in live_url
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'bye'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' not in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Bar' in [x.text for x in resp.pyquery('p.label')]
    assert 'Foo' not in [x.text for x in resp.pyquery('p.label')]


def test_backoffice_submission_conditional_jump_based_on_bo_field(pub):
    user = create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='form-title')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo0', varname='foo_bovar', label='bo variable'),
    ]

    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    setbo = st1.add_action('set-backoffice-fields')
    setbo.fields = [{'field_id': 'bo0', 'value': 'go'}]

    jump = st1.add_action('jump')
    jump.condition = {'type': 'django', 'value': "form_var_foo_bovar == 'go'"}
    jump.status = 'st2'

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.TextField(id='1', varname='foo', label='fo variable'),
        fields.TextField(id='2', varname='var3', label='n/a', condition={'type': 'django', 'value': 'True'}),
    ]
    formdef.workflow_id = workflow.id
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()
    formdef.data_class().wipe()

    # check jump condition being True
    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foo'
    resp.form['f2'] = 'bar'
    resp = resp.form.submit('submit')  # -> confirmation page
    resp = resp.form.submit('submit').follow()

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-st2'

    # check jump condition being False
    setbo.fields = [{'field_id': 'bo0', 'value': 'nogo'}]
    workflow.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foo'
    resp.form['f2'] = 'bar'
    resp = resp.form.submit('submit')  # -> confirmation page
    resp = resp.form.submit('submit').follow()

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-st1'


def test_backoffice_submission_drafts_order(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata_ids = []
    for i in range(25):
        formdata = data_class()
        formdata.data = {}
        formdata.status = 'draft'
        formdata.backoffice_submission = True
        formdata.receipt_time = make_aware(datetime.datetime(2023, 11, 30 - i))
        formdata.store()
        formdata_ids.append(formdata.id)

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/pending')
    assert [x.attrib['data-link'] for x in resp.pyquery('tbody tr')] == [
        f'form-title/{x}/' for x in formdata_ids[:20]
    ]

    formdata.receipt_time = None  # check a missing receipt_time is ok
    formdata.store()
    new_order = [formdata.id] + [x for x in formdata_ids if x != formdata.id]
    resp = app.get('/backoffice/submission/pending')
    assert [x.attrib['data-link'] for x in resp.pyquery('tbody tr')] == [
        f'form-title/{x}/' for x in new_order[:20]
    ]

    resp = resp.click('<!--Next Page-->')
    assert [x.attrib['data-link'] for x in resp.pyquery('tbody tr')] == [
        f'form-title/{x}/' for x in new_order[20:]
    ]

    # check ajax call result
    resp = app.get('/backoffice/submission/pending?ajax=true')
    assert 'appbar' not in resp.text
    assert '<table' in resp.text
    assert 'page-links' in resp.text


def test_backoffice_submission_prefill_user(pub):
    user = create_user(pub)
    other_user = pub.user_class(name='other user')
    other_user.email = 'other@example.net'
    other_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='1st field',
            display_locations=['validation', 'summary', 'listings'],
            prefill={'type': 'user', 'value': 'email'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.backoffice_submission = True
    formdata.status = 'draft'
    formdata.data = {}
    formdata.submission_channel = 'mail'
    formdata.user_id = other_user.id
    formdata.submission_context = {}
    formdata.store()

    formdata2 = formdef.data_class()()
    formdata2.backoffice_submission = True
    formdata2.status = 'draft'
    formdata2.data = {}
    formdata.submission_channel = 'mail'
    formdata.user_id = None
    formdata2.submission_context = {}
    formdata2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    assert resp.form['f1'].value == ''

    # restore a draft
    resp = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp = resp.follow()
    # and check it got prefilled with the user from context
    assert resp.form['f1'].value == 'other@example.net'

    # restore another, without user id
    resp = app.get('/backoffice/submission/form-title/%s/' % formdata2.id)
    resp = resp.follow()
    # and check it was not prefilled
    assert resp.form['f1'].value == ''


@pytest.mark.parametrize('enable_tracking_code', [True, False])
def test_backoffice_submission_prefill_user_multiple_pages(pub, autosave, enable_tracking_code):
    user = create_user(pub)
    other_user = pub.user_class(name='other user')
    other_user.email = 'other@example.net'
    other_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='1st field', required='optional'),
        fields.PageField(id='4', label='2nd page'),
        fields.StringField(id='5', label='field on 2nd page', prefill={'type': 'user', 'value': 'email'}),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = enable_tracking_code
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.backoffice_submission = True
    formdata.status = 'draft'
    formdata.data = {}
    formdata.submission_channel = 'mail'
    formdata.user_id = other_user.id
    formdata.submission_context = {}
    formdata.store()

    formdata2 = formdef.data_class()()
    formdata2.backoffice_submission = True
    formdata2.status = 'draft'
    formdata2.data = {}
    formdata2.submission_channel = 'mail'
    formdata2.user_id = None
    formdata2.submission_context = {}
    formdata2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp = resp.form.submit('submit')
    assert resp.form['f5'].value == ''

    # restore a draft
    resp = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp = resp.follow()
    resp = resp.form.submit('submit')
    # and check it got prefilled with the user from context
    assert resp.form['f5'].value == 'other@example.net'

    # restore another, without user id
    resp = app.get('/backoffice/submission/form-title/%s/' % formdata2.id)
    resp = resp.follow()
    resp = resp.form.submit('submit')
    # and check it was not prefilled
    assert resp.form['f5'].value == ''

    # continue with additional tests when drafts are enabled, using autosave
    if not enable_tracking_code:
        return

    # restore a draft
    formdata.page_no = 0
    formdata.user_id = other_user.id
    formdata.store()
    resp = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp = resp.follow()
    resp.form['f1'] = 'Hello'
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
    assert formdef.data_class().get(formdata.id).user_id == str(other_user.id)
    assert formdef.data_class().get(formdata.id).data['1'] == 'Hello'

    resp = resp.form.submit('submit')
    # and check it got prefilled with the user from context
    assert resp.form['f5'].value == 'other@example.net'

    assert formdef.data_class().get(formdata.id).user_id == str(other_user.id)
    assert formdef.data_class().get(formdata.id).data['1'] == 'Hello'

    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'Hello'
    app.post('/backoffice/submission/form-title/autosave', params=resp.form.submit_fields())
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].user_id is None
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].user_id is None


def test_backoffice_submission_multiple_page_restore_on_validation(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='1st field', required='optional'),
        fields.PageField(id='2', label='2nd page', condition={'type': 'django', 'value': 'False'}),
        fields.PageField(id='3', label='3rd page'),
        fields.StringField(id='5', label='field on 3rd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foo'
    resp = resp.form.submit('submit')
    resp.form['f5'] = 'bar'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    # restore draft
    resp = app.get('/backoffice/submission/pending')
    resp = resp.click(href='form-title/%s' % formdata.id)
    resp = resp.follow()
    assert 'Check values then click submit.' in resp.text


def test_backoffice_submission_substitution_vars(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='1st field', required='optional', varname='foobar'),
        fields.ItemField(id='10', label='2nd field', items=['foo', 'bar', 'baz'], varname='foobar2'),
        fields.PageField(id='4', label='2nd page'),
        fields.CommentField(id='5', label='X[form_var_foobar]Y[form_var_foobar2_raw]Z'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'].value = 'PLOP'
    resp.form['f10'].value = 'bar'
    resp = resp.form.submit('submit')
    assert 'XPLOPYbarZ' in resp.text

    # django-templated comment
    formdef.fields[4] = fields.CommentField(
        id='5', label='dj-{{ form_var_foobar }}-an-{{ form_var_foobar2_raw}}-go'
    )
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'].value = 'foo'
    resp.form['f10'].value = 'bar'
    resp = resp.form.submit('submit')
    assert 'dj-foo-an-bar-go' in resp.text

    formdef.data_class().wipe()

    # same but starting from a draft, as if it was initiated by welco
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.status = 'draft'
    formdata.backoffice_submission = True
    formdata.store()

    resp = app.get('/backoffice/submission/form-title/%s/' % formdata.id)
    resp = resp.follow()
    resp.form['f1'].value = 'PLOP'
    resp.form['f10'].value = 'bar'
    resp = resp.form.submit('submit')
    assert 'dj-PLOP-an-bar-go' in resp.text


def test_backoffice_submission_manual_channel(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    assert formdef.url_name in resp.text

    resp = resp.click(formdef.name)
    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['submission_channel'] = 'mail'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).data['1'] == 'test submission'
    assert data_class.get(formdata_no).status == 'wf-new'
    assert data_class.get(formdata_no).user is None
    assert data_class.get(formdata_no).backoffice_submission is True
    assert data_class.get(formdata_no).submission_channel == 'mail'


def test_backoffice_submission_recall_manual_channel(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='page 1'),
        fields.StringField(id='2', label='1st field'),
        fields.PageField(id='3', label='page 2'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['submission_channel'] = 'mail'  # set via js
    resp.form['f2'] = 'test submission'
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/submission/pending')
    resp = resp.click(href=resp.pyquery('table a').attr.href)
    resp = resp.follow()
    assert resp.pyquery('.submit-channel-selection option[selected]').attr.value == 'mail'
    resp.form['submission_channel'] = 'email'  # set via js
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('previous')  # -> back to page 2
    assert resp.pyquery('.submit-channel-selection option[selected]').attr.value == 'email'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].submission_channel == 'email'


def test_backoffice_submission_manual_channel_with_return_url(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': '23'}  # role the user doesn't have
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/%s/?ReturnURL=http://example.net' % formdef.url_name)
    resp = resp.follow().follow()

    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['submission_channel'] = 'mail'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text

    # final submit
    resp = resp.form.submit('submit')
    # as the user doesn't have a role to view the submitted form there's a
    # redirection to the preset URL.
    assert resp.location == 'http://example.net'


def test_backoffice_submission_with_nameid_and_channel(pub, local_user):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='1st field',
            display_locations=['validation', 'summary', 'listings'],
            prefill={'type': 'string', 'value': '{{form_user_email|default:""}}'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(
        '/backoffice/submission/form-title/?NameID=%s&channel=mail' % local_user.name_identifiers[0]
    )
    assert resp.location.startswith('http://example.net/backoffice/submission/form-title/')

    formdata_no = resp.location.split('/')[-2]
    formdata = formdef.data_class().get(formdata_no)
    assert formdata.user_id == str(local_user.id)
    assert formdata.submission_channel == 'mail'
    assert formdata.status == 'draft'

    resp = resp.follow()  # redirect to created draft
    resp = resp.follow()  # redirect to ?mt=

    # check user is mentioned in sidebar
    assert '<h3>Associated User</h3>' in resp
    assert '<p>%s</p>' % local_user.get_display_name() in resp

    assert resp.form['f1'].value == local_user.email  # prefill with form_user_email
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    formdata = data_class.get(formdata_no)
    assert formdata.user_id == str(local_user.id)
    assert formdata.submission_channel == 'mail'
    assert formdata.status == 'wf-new'

    # target user is unknown
    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/?NameID=UNKNOWN_NAMEID')
    assert resp.location.startswith('http://example.net/backoffice/submission/form-title/')
    formdata_no = resp.location.split('/')[-2]
    formdata = formdef.data_class().get(formdata_no)
    assert not formdata.user_id
    resp = resp.follow()  # redirect to created draft
    resp = resp.follow()  # redirect to ?mt=
    assert 'The target user was not found, this form is anonymous.' in resp.text


def test_backoffice_submission_with_nameid_and_extra_query_string(pub, local_user):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
        ),
        fields.StringField(
            id='2',
            label='field',
            prefill={'type': 'string', 'value': 'x{{ form_var_computed }}y'},
        ),
        fields.StringField(
            id='3',
            label='field',
            prefill={'type': 'string', 'value': 'x{{ request.GET.param }}y'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/?NameID=%s&param=test' % local_user.name_identifiers[0])
    assert resp.location.startswith('http://example.net/backoffice/submission/form-title/')

    formdata_no = resp.location.split('/')[-2]
    formdata = formdef.data_class().get(formdata_no)
    assert formdata.user_id == str(local_user.id)
    resp = resp.follow()  # redirect to created draft
    resp = resp.follow()  # redirect to ?mt=

    # check user is mentioned in sidebar
    assert '<h3>Associated User</h3>' in resp
    assert '<p>%s</p>' % local_user.get_display_name() in resp

    assert resp.form['f2'].value == 'xtesty'  # prefill with computed string (= request.GET)
    assert resp.form['f3'].value == 'xtesty'  # prefill with request.GET
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    formdata = data_class.get(formdata_no)
    assert formdata.user_id == str(local_user.id)
    assert formdata.data == {'1': 'test', '2': 'xtesty', '3': 'xtesty'}


def test_backoffice_submission_only_one_check(pub, local_user):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.only_allow_one = True
    formdef.store()

    formdef.data_class().wipe()

    # create a formdata attached the agent
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 2

    # initiate a submission associated to a user
    resp = app.get('/backoffice/submission/form-title/?NameID=%s' % local_user.name_identifiers[0])
    resp = resp.follow().follow()
    assert 'This form is limited to one per user' not in resp
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 3

    # initiate a second one
    resp = app.get('/backoffice/submission/form-title/?NameID=%s' % local_user.name_identifiers[0])
    resp = resp.follow().follow()
    assert 'This form is limited to one per user' in resp


def test_backoffice_submission_channel_selection(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    assert resp.pyquery('.submit-channel-selection')
    resp.form['submission_channel'] = 'counter'  # happens via javascript
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-channel-selection option[selected=selected]')[0].attrib['value'] == 'counter'
    assert resp.form['submission_channel'].value == 'counter'
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Counter</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'counter'

    # select channel on second page
    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-channel-selection')
    resp.form['submission_channel'] = 'counter'  # happens via javascript
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Counter</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'counter'

    # with preset channel
    resp = app.get('/backoffice/submission/%s/?channel=counter' % formdef.url_name)
    resp = resp.follow().follow()
    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert not resp.pyquery('.submit-channel-selection')
    assert '<h3>Channel: Counter</h3>' in resp
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Counter</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'counter'

    # with submission_context but not submission channel
    resp = app.get('/backoffice/submission/%s/?ReturnURL=https://example.org' % formdef.url_name)
    resp = resp.follow().follow()
    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-channel-selection')
    resp.form['submission_channel'] = 'counter'  # happens via javascript
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Counter</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'counter'


def test_backoffice_submission_channel_post_condition_check(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_submission_channel'},
                    'error_message': 'a channel must be selected',
                }
            ],
        ),
        fields.StringField(id='1', label='Field on 1st page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> error
    assert 'a channel must be selected' in resp.text

    resp.form['submission_channel'] = 'phone'  # happens via javascript when a channel is selected in sidebar
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Phone</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'phone'

    # check if it's selected on a second page
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(
            id='2',
            label='2nd page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_submission_channel'},
                    'error_message': 'a channel must be selected',
                }
            ],
        ),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> second page
    resp.form['f3'] = 'test submission'
    resp = resp.form.submit('submit')  # -> error
    assert 'a channel must be selected' in resp.text

    resp.form['submission_channel'] = 'phone'  # happens via javascript when a channel is selected in sidebar
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<h3>Channel: Phone</h3>' in resp
    resp = resp.form.submit('submit')  # final submit
    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).submission_channel == 'phone'


def test_backoffice_submission_user_selection(pub):
    user = create_user(pub)

    for i in range(10):
        random_user = pub.user_class()
        random_user.name = 'random user %s' % i
        random_user.email = 'test%s@invalid' % i
        random_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)

    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-user-selection')
    assert resp.pyquery('.submit-user-selection option').attr('value') == str(random_user.id)
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<p>%s</p>' % random_user.name in resp

    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).user.name == random_user.name

    # select user on second page
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)

    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<p>%s</p>' % random_user.name in resp

    # final submit
    resp = resp.form.submit('submit')

    formdata_no = resp.location.split('/')[-2]
    data_class = formdef.data_class()
    assert data_class.get(formdata_no).user.name == random_user.name

    # check prefill
    formdef.fields[-1].prefill = {'type': 'user', 'value': 'email'}
    formdef.store()

    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)

    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('.submit-user-selection')
    assert resp.pyquery('.submit-user-selection option').attr('value') == str(random_user.id)
    assert resp.form['f3'].value == random_user.email
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert '<p>%s</p>' % random_user.name in resp

    # check user name appears in pending table
    resp = app.get('/backoffice/submission/pending')
    assert [x.text for x in resp.pyquery('tbody td')[-2:]] == ['admin', 'random user 9']


def test_backoffice_submission_recall_user_selection(pub):
    user = create_user(pub)
    random_user = pub.user_class()
    random_user.name = 'random user'
    random_user.email = 'test@invalid'
    random_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='page 1'),
        fields.StringField(id='2', label='1st field'),
        fields.PageField(id='3', label='page 2'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp.form['f2'] = 'test submission'
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/submission/pending')
    resp = resp.click(href=resp.pyquery('table a').attr.href)
    resp = resp.follow()
    assert resp.pyquery('.user-selection option').text() == 'random user'
    resp.form['user_id'] = str(user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('previous')  # -> back to page 2
    assert resp.pyquery('.user-selection option').text() == 'admin'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].user_id == str(user.id)


def test_backoffice_submission_required_user_selection(pub):
    user = create_user(pub)

    user1 = pub.user_class()
    user1.name = 'user1'
    user1.roles = []
    user1.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))

    # any user, optional
    resp = app.get(formdef.get_submission_url(backoffice=True))
    assert resp.pyquery('.submit-user-selection')
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '2'
    assert not resp.pyquery('.global-errors').text()

    # any user but required
    formdef.submission_user_association = 'any-required'
    formdef.store()
    resp = app.get(formdef.get_submission_url(backoffice=True))
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    # 2nd page but with a message about associating an user
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '2'
    assert resp.pyquery('.global-errors').text() == 'The form must be associated to an user.'
    resp.form['f3'] = 'test submission'
    resp = resp.form.submit('submit')  # -> still on second page (with error)
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '2'
    assert resp.pyquery('.global-errors').text() == 'The form must be associated to an user.'
    resp.form['user_id'] = str(user1.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '3'
    assert not resp.pyquery('.global-errors').text()
    resp = resp.form.submit('submit', status=302)  # -> submit

    # formdef with no pages
    formdef.fields = [
        fields.StringField(id='1', label='Field on 1st page'),
    ]
    formdef.store()

    resp = app.get(formdef.get_submission_url(backoffice=True))
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 1st page, still error
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '1'
    assert resp.pyquery('.global-errors').text() == 'The form must be associated to an user.'
    resp.form['user_id'] = str(user1.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '2'
    assert not resp.pyquery('.global-errors').text()
    resp = resp.form.submit('submit', status=302)  # -> submit

    # formdef with no confirmation page
    formdef.confirmation = False
    formdef.store()
    resp = app.get(formdef.get_submission_url(backoffice=True))
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 1st page, still error
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '1'
    assert resp.pyquery('.global-errors').text() == 'The form must be associated to an user.'
    resp.form['user_id'] = str(user1.id)  # happens via javascript
    resp = resp.form.submit('submit', status=302)  # -> submit


def test_backoffice_submission_required_user_with_role_selection(pub):
    user = create_user(pub)

    role1 = pub.role_class(name='foo')
    role1.store()
    role2 = pub.role_class(name='foo2')
    role2.store()

    user1 = pub.user_class()
    user1.name = 'user1'
    user1.roles = [role1.id]
    user1.store()

    user2 = pub.user_class()
    user2.name = 'user2'
    user2.roles = [role2.id]
    user2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.roles = [role1.id]
    formdef.submission_user_association = 'roles'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_submission_url(backoffice=True))
    assert resp.pyquery('.submit-user-selection select').attr['data-users-api-roles'] == str(role1.id)
    assert [x['user_display_name'] for x in app.get(f'/api/users/?roles={role1.id}').json['data']] == [
        'user1'
    ]

    formdef.roles = [role1.id, role2.id]
    formdef.store()
    resp = app.get(formdef.get_submission_url(backoffice=True))
    assert (
        resp.pyquery('.submit-user-selection select').attr['data-users-api-roles'] == f'{role1.id},{role2.id}'
    )
    assert [
        x['user_display_name'] for x in app.get(f'/api/users/?roles={role1.id},{role2.id}').json['data']
    ] == ['user1', 'user2']

    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    # 2nd page but with a message about associating an user
    assert resp.pyquery('#steps .current .wcs-step--marker-nb').text() == '2'
    assert resp.pyquery('.global-errors').text() == 'The form must be associated to an user.'


def test_backoffice_submission_user_selection_then_live(pub):
    user = create_user(pub)

    for i in range(10):
        random_user = pub.user_class()
        random_user.name = 'random user %s' % i
        random_user.email = 'test%s@invalid' % i
        random_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3',
            label='Field on 2nd page',
            varname='plop',
            prefill={'type': 'user', 'value': 'email'},
        ),
        fields.StringField(
            id='4',
            label='2nd field on 2nd page',
            prefill={'type': 'string', 'value': '{{form_user_email}}', 'locked': True},
        ),
        fields.StringField(
            id='5',
            label='field with condition',
            condition={'type': 'django', 'value': 'form_var_plop == "bye"'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)

    assert resp.form['submission_channel'].attrs['type'] == 'hidden'
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == random_user.email
    assert resp.form['f4'].value == random_user.email

    resp.form['f4'] = 'altered value'  # alter value

    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp
    assert 'altered value' not in resp


def test_backoffice_submission_user_selection_then_live_prefill(pub):
    user = create_user(pub)

    for i in range(10):
        random_user = pub.user_class()
        random_user.name = 'random user %s' % i
        random_user.email = 'test%s@invalid' % i
        random_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(
            id='3',
            label='Field on 2nd page',
            varname='plop',
            prefill={'type': 'user', 'value': 'email'},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    url = '/backoffice/submission/%s/' % formdef.url_name
    resp = app.get(url)

    live_url = resp.html.find('form').attrs['data-live-url']
    assert resp.pyquery('.submit-user-selection')
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', 'user')])
    assert live_resp.json == {'result': {'3': {'visible': True, 'content': 'test9@invalid', 'locked': False}}}

    # check with locked field
    formdef.fields[1].prefill['locked'] = True
    formdef.store()
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', 'user')])
    assert live_resp.json == {'result': {'3': {'visible': True, 'content': 'test9@invalid', 'locked': True}}}


def test_backoffice_submission_user_selection_then_card_data_source(pub):
    pub.session_manager.session_class.wipe()

    user = create_user(pub)

    for i in range(10):
        random_user = pub.user_class()
        random_user.name = 'random user %s' % i
        random_user.email = 'test%s@invalid' % i
        random_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.digest_templates = {'default': 'card {{ form_number }}'}
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.user_support = 'optional'
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {}
    carddata.user_id = user.id
    carddata.just_created()
    carddata.store()

    for i in range(3):
        carddata = carddef.data_class()()
        carddata.data = {}
        carddata.user_id = random_user.id
        carddata.just_created()
        carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-user': 'on',
        'filter-user-value': '__current__',
    }
    custom_view.store()

    Workflow.wipe()
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemField(
            id='3',
            label='Test',
            data_source={'type': 'carddef:foo:%s' % custom_view.slug, 'value': ''},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)

    resp = resp.form.submit('submit')  # -> 2nd page
    # check the field doesn't propose cards linked to agent
    assert resp.form['f3'].options == [('', False, '---')]

    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> 2nd page
    # check three cards are proposed
    assert len(resp.form['f3'].options) == 3
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['3_display'] == f'card {carddef.id}-2'

    # check it's also ok during edition
    resp = app.get(formdata.get_backoffice_url())
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    resp = resp.form.submit('submit')  # page 2
    assert len(resp.form['f3'].options) == 3


def test_backoffice_submission_user_selection_then_user_function_filter(pub):
    pub.session_manager.session_class.wipe()

    user = create_user(pub)

    role = pub.role_class(name='foo')
    role.store()

    random_user = pub.user_class()
    random_user.name = 'random user'
    random_user.email = 'test@invalid'
    random_user.roles = [role.id]
    random_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.digest_templates = {'default': 'card {{ form_number }}'}
    carddef.backoffice_submission_roles = user.roles
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.user_support = 'optional'
    carddef.store()

    for i in range(3):
        carddata = carddef.data_class()()
        carddata.data = {}
        carddata.just_created()
        if i == 1:
            carddata.workflow_roles = {'_editor': random_user.roles}
        carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared custom test view'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-user-function': 'on',
        'filter-user-function-value': '_editor',
    }
    custom_view.store()

    Workflow.wipe()
    workflow = Workflow(name='wfedit')
    st1 = workflow.add_status('Status1')
    wfedit = st1.add_action('editable', id='_wfedit')
    wfedit.by = [user.roles[0]]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemField(
            id='3',
            label='Test',
            data_source={'type': 'carddef:foo:%s' % custom_view.slug, 'value': ''},
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)

    resp = resp.form.submit('submit')  # -> 2nd page
    # check the field doesn't propose cards linked to agent
    assert resp.form['f3'].options == [('', False, '---')]

    formdef.data_class().wipe()
    resp = app.get('/backoffice/submission/%s/' % formdef.url_name)
    resp.form['user_id'] = str(random_user.id)  # happens via javascript
    resp = resp.form.submit('submit')  # -> 2nd page
    # check a single card is proposed
    assert len(resp.form['f3'].options) == 1
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['3_display'] == f'card {carddef.id}-2'

    # check it's also ok during edition
    resp = app.get(formdata.get_backoffice_url())
    resp = resp.form.submit('button_wfedit')
    resp = resp.follow()
    resp = resp.form.submit('submit')  # page 2
    assert len(resp.form['f3'].options) == 1


def test_backoffice_submission_sidebar_lateral_block(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field', varname='foo')]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    assert '/lateral-block' not in resp.text

    formdef.submission_lateral_template = 'foo bar blah'
    formdef.store()

    def get_lateral_block_url(resp):
        return app.get(re.findall('data-async-url="(.*/lateral-block.*?)"', resp.text)[0])

    resp = app.get('/backoffice/submission/form-title/')
    partial_resp = get_lateral_block_url(resp)
    assert partial_resp.text == '<div class="lateral-block">foo bar blah</div>'

    # form in lateral template
    formdef.submission_lateral_template = (
        'foo {{ form_status }} - {{ form_submission_agent_name }} - x{{ form_var_foo }}y'
    )
    formdef.store()
    resp = app.get('/backoffice/submission/form-title/')
    partial_resp = get_lateral_block_url(resp)
    assert partial_resp.text == '<div class="lateral-block">foo Draft - admin - xNoney</div>'

    resp.form['f1'] = 'blah'
    resp = resp.form.submit('submit')  # -> validation page
    partial_resp = get_lateral_block_url(resp)
    assert partial_resp.text == '<div class="lateral-block">foo Draft - admin - xblahy</div>'

    # webservice in lateral template
    NamedWsCall.wipe()
    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()

    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.example.net/json', json={'foo': 'bar'})
        formdef.submission_lateral_template = 'XX{{webservice.hello_world.foo}}XX'
        formdef.store()
        resp = app.get('/backoffice/submission/form-title/')
        partial_resp = get_lateral_block_url(resp)
        assert partial_resp.text == '<div class="lateral-block">XXbarXX</div>'

    # error in lateral template
    formdef.submission_lateral_template = 'XX{{ }}XX'
    formdef.store()

    LoggedError.wipe()
    resp = app.get('/backoffice/submission/form-title/')
    partial_resp = get_lateral_block_url(resp)
    assert partial_resp.text == ''
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == 'Could not render submission lateral template (syntax error in Django template: Empty variable tag on line 1)'
    )

    formdef.submission_lateral_template = 'XX{{ "a"|add:bar }}XX'
    formdef.store()

    LoggedError.wipe()
    resp = app.get('/backoffice/submission/form-title/')
    partial_resp = get_lateral_block_url(resp)
    assert partial_resp.text == ''
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == "Could not render submission lateral template (missing variable \"bar\" in template)"
    )


def test_backoffice_submission_computed_field(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='{{ "xxx" }}'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'xxx'}


def test_backoffice_submission_parent_var(pub):
    user = create_user(pub)

    FormDef.wipe()
    Workflow.wipe()

    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.workflow_roles = {'_receiver': user.roles[0]}
    source_formdef.fields = [
        fields.StringField(id='0', label='string', varname='toto_string'),
    ]
    source_formdef.store()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.workflow_roles = {'_receiver': user.roles[0]}
    target_formdef.backoffice_submission_roles = user.roles[:]
    target_formdef.fields = [
        fields.PageField(id='0', label='page 1'),
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ form_parent_form_var_toto_string }}',
        ),
        fields.CommentField(id='2', label='parent:{{ form_parent_form_var_toto_string }}'),
        fields.CommentField(id='3', label='computed:{{ form_var_computed }}'),
    ]
    target_formdef.store()

    # workflow
    wf = Workflow(name='create-formdata')
    st1 = wf.add_status('New')
    create_formdata = st1.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.draft = True
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.user_association_mode = 'keep-user'
    create_formdata.backoffice_submission = True
    create_formdata.attach_to_history = True
    create_formdata.map_fields_by_varname = True
    wf.store()

    source_formdef.workflow = wf
    source_formdef.store()

    # create source formdata
    formdata = source_formdef.data_class()()
    formdata.data = {'0': 'foobar'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    # login and go to backoffice management page
    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())

    # click on target form in history
    resp = resp.click(href=r'/target-form/')
    resp = resp.follow().follow()
    assert 'parent:foobar' in resp.text  # parent var is ok
    assert 'computed:foobar' in resp.text  # and getting it via a computed var is also ok


@pytest.mark.parametrize('default_screen', ['new', 'pending'])
def test_backoffice_submission_no_roles(pub, default_screen):
    pub.cfg['backoffice-submission'] = {'default_screen': default_screen}
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='1st field'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.roles = ['XXX']  # role the agent doesn't have
    formdef.workflow_roles = {}
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click('form title')
    resp.forms[0]['f1'] = 'xxx'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    resp = resp.follow()  # -> new or pending
    assert resp.pyquery('.messages .success').text() == 'Submitted form has been recorded.'
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'xxx'}


def test_backoffice_submission_then_front(pub):
    user = create_user(pub)

    front_user = pub.user_class()
    front_user.name = 'front user'
    front_user.email = 'test@invalid'
    front_user.store()
    account = PasswordAccount(id='front')
    account.set_password('front')
    account.user_id = front_user.id
    account.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Field on 2nd page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/new')
    resp = resp.click(formdef.name)

    resp.form['user_id'] = str(front_user.id)  # happens via javascript
    resp.form['submission_channel'] = 'phone'
    resp.form['f1'] = 'test submission'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp.form['f3'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # final submit

    formdata = formdef.data_class().get(resp.location.split('/')[-2])
    resp = login(get_app(pub), username='front', password='front').get(formdata.get_url())
    assert (
        resp.pyquery('.text-form-recorded').text()
        == f'The form has been recorded on {formdata.receipt_time.strftime("%Y-%m-%d %H:%M")} '
        f'with the number {formdata.get_display_id()}. It has been submitted for you by '
        f'admin after a phone call.'
    )


def test_backoffice_submission_sidebar_elements(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Field on 1st page'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_submission_url(backoffice=True))
    assert resp.pyquery('.submit-user-selection')

    formdef.submission_sidebar_items = ['general', 'custom-template']
    formdef.store()
    resp = app.get(formdef.get_submission_url(backoffice=True))
    assert not resp.pyquery('.submit-user-selection')


def test_backoffice_submission_previous_on_submitted_draft(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'foobar2'
    resp = resp.form.submit('submit')  # -> validation
    resp.form.submit('submit').follow()  # -> submit

    # simulate the user going back and then clicking on previous
    resp = resp.form.submit('previous').follow()
    assert resp.request.url == 'http://example.net/backoffice/submission/form-title/'
    assert 'This form has already been submitted.' in resp.text

    # again but simulate browser stuck on the validation page while the form
    # is being recorded and the magictoken not yet being removed when the user
    # clicks the "previous page" button
    resp = app.get('/backoffice/submission/form-title/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # -> page 2
    resp.form['f3'] = 'foobar2'
    resp = resp.form.submit('submit')  # -> validation

    with mock.patch('wcs.sql.Session.remove_magictoken') as remove_magictoken:
        resp.form.submit('submit').follow()  # -> submit
        assert remove_magictoken.call_count == 1

    resp = resp.form.submit('previous').follow()  # -> page 2
    assert resp.request.url == 'http://example.net/backoffice/submission/form-title/'
    assert 'This form has already been submitted.' in resp.text


def test_backoffice_submission_with_async_workflow_processing(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'perform-workflow-as-job', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))

    # do not let tests run afterjobs synchronously
    with mock.patch('wcs.qommon.publisher.QommonPublisher.process_after_jobs'):
        resp = app.get('/backoffice/submission/form-title/')
        resp.form['f1'] = 'test submission'
        resp = resp.form.submit('submit')  # -> to validation
        AfterJob.wipe()
        resp = resp.form.submit('submit').follow()  # -> to submit
        afterjob = AfterJob.select()[0]
        assert afterjob.label == 'Processing'
        assert afterjob.status == 'registered'
        assert resp.pyquery('[data-workflow-processing="true"]')
        afterjob_id = resp.pyquery('[data-workflow-processing-afterjob-id]').attr[
            'data-workflow-processing-afterjob-id'
        ]
        assert afterjob.id == afterjob_id
        assert resp.pyquery('.busy-processing').text() == 'Processing...'

    formdata = formdef.data_class().select()[0]

    assert app.get(formdata.get_backoffice_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'processing',
        'job': {'status': 'registered', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    pub.after_jobs.append(afterjob)
    pub.process_after_jobs()
    assert app.get(formdata.get_backoffice_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'idle',
        'job': {'status': 'completed', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    resp = app.get(formdata.get_backoffice_url())
    assert not resp.pyquery('[data-workflow-processing="true"]')


def test_backoffice_submission_required_only_in_frontoffice(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.StringField(id='1', label='1st field', required='frontoffice')]
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_submission_url(backoffice=True))
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()

    resp = app.get(formdef.get_submission_url(backoffice=False))
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_f1').text() == 'required field'
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()


def test_backoffice_submission_draft_page_id(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ComputedField(
            id='1', label='Computed', varname='computed', value_template='{{ request.GET.computed }}'
        ),
        fields.StringField(
            id='2',
            label='string',
            prefill={'type': 'string', 'value': '{{form_user_email|default:""}}'},
        ),
        fields.PageField(id='3', label='2nd page'),
        fields.StringField(id='4', label='string 2'),
    ]
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.enable_tracking_codes = True
    formdef.store()
    first_page_id = formdef.fields[0].id
    second_page_id = formdef.fields[3].id

    data_class = formdef.data_class()
    data_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/form-title/')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '0'
    assert formdata.page_id == first_page_id

    resp.form['f2'] = 'foobar'
    resp = resp.form.submit('submit')  # -> page 2
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdata.page_no == '1'
    assert formdata.page_id == second_page_id

    resp.form['f4'] = 'foobar2'
    resp = resp.form.submit('submit')  # -> validation
    resp.form.submit('submit').follow()  # -> submit
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-new'


def test_backoffice_pending_submissions_agent_filter(pub):
    user = create_user(pub)

    other_user = pub.user_class(name='other user')
    other_user.email = 'other@example.net'
    other_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.backoffice_submission_roles = user.roles[:]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.status = 'draft'
    formdata.backoffice_submission = True
    formdata.submission_agent_id = str(user.id)
    formdata.store()

    formdata2 = formdef.data_class()()
    formdata2.status = 'draft'
    formdata2.backoffice_submission = True
    formdata2.submission_agent_id = str(other_user.id)
    formdata2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/pending')
    assert [PyQuery(x).find('td:nth-child(3)').text() for x in resp.pyquery('tbody tr')] == [
        'admin',
        'other user',
    ]
    assert not resp.forms['listing-settings']['mine'].value

    # (javascript is setting query string)
    resp = app.get('/backoffice/submission/pending?mine=true')
    assert [PyQuery(x).find('td:nth-child(3)').text() for x in resp.pyquery('tbody tr')] == ['admin']
    assert resp.forms['listing-settings']['mine'].value == 'true'
