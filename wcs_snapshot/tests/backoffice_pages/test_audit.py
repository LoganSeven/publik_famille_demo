import datetime
import os

import pytest
from django.utils.timezone import localtime

from wcs.audit import Audit
from wcs.carddef import CardDef
from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.qommon import audit
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def superuser(pub):
    return create_superuser(pub)


@pytest.fixture
def pub(request, emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_formdata_audit(pub, superuser):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    app.get(formdef.get_url(backoffice=True))
    app.get(formdata.get_backoffice_url())

    assert Audit.count() == 2
    audit1, audit2 = Audit.select(order_by='id')
    assert audit1.action == 'listing'
    assert audit2.action == 'view'
    assert audit1.object_type == audit2.object_type == 'formdef'
    assert audit1.object_id == audit2.object_id == str(formdef.id)
    assert audit1.data_id is None
    assert audit2.data_id == formdata.id

    assert audit2.frozen['user_email'] == superuser.email
    assert audit2.frozen['object_slug'] == formdef.slug


def test_carddata_audit(pub, superuser):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.id_template = 'card-{{ form_var_foo }}'
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'bar'}
    carddata.just_created()
    carddata.store()

    Audit.wipe()
    audit('view', obj=carddata)
    app = login(get_app(pub))
    resp = app.get('/backoffice/journal/')
    assert resp.pyquery('.journal-table--description').text() == 'View Data - foo - card-bar'


def test_audit_journal(pub, superuser):
    Audit.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    formdef2 = FormDef()
    formdef2.name = 'form title 2'
    formdef2.store()
    formdef2.data_class().wipe()
    formdata2 = formdef2.data_class()()
    formdata2.just_created()
    formdata2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/journal/')  # visit empty
    assert resp.pyquery('tbody tr').length == 0

    for i in range(5):
        audit('listing', obj=formdef, user_id=superuser.id)
    for i in range(50):
        audit('view', obj=formdata, user_id=superuser.id)

    # create audit object in the past
    audit_obj = Audit.select(order_by='-id')[0]
    audit_obj.id = None
    audit_obj.timestamp = audit_obj.timestamp - datetime.timedelta(days=40)
    audit_obj.store()

    # additional audit events
    audit('export.csv', obj=formdef, user_id=superuser.id)
    audit('export.csv', obj=formdef2, user_id=superuser.id)
    audit('download file', obj=formdata2, user_id=superuser.id, extra_label='file.png')
    audit('settings', cfg_key='filetypes')

    resp = app.get('/backoffice/studio/')
    resp = resp.click('Audit Journal')
    assert resp.pyquery('.journal-table--user:first').text() == 'admin'
    assert resp.pyquery('tbody tr').length == 10
    resp = resp.click('Next page')
    assert resp.pyquery('tbody tr').length == 10
    resp = resp.click('Previous page')
    assert resp.pyquery('tbody tr').length == 10
    resp = resp.click('First page')
    assert resp.pyquery('tbody tr').length == 10
    assert resp.pyquery('.button.first-page.disabled')

    resp = resp.click('Last page')
    assert resp.pyquery('tbody tr').length == 10
    assert resp.pyquery('.button.last-page.disabled')

    resp.form['date'] = localtime(audit_obj.timestamp).strftime('%Y-%m-%d')
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 1

    resp.form['date'] = ''
    resp.form['action'] = 'listing'
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 5

    resp.form['user_id'].force_value('12')
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 0

    resp.form['user_id'].force_value(superuser.id)
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 5
    assert resp.form['user_id'].value == str(superuser.id)
    assert resp.form['user_id'].options[-1] == (str(superuser.id), True, 'admin')

    resp.form['action'] = 'export.csv'
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 2

    assert resp.pyquery('[data-widget-name="object_id"]').attr.style == 'display: none'
    resp.form['object'] = f'formdef:{formdef2.id}'
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 1
    assert resp.pyquery('[data-widget-name="object_id"]').attr.style != 'display: none'
    assert resp.pyquery('.button.last-page.disabled')

    resp.form['action'] = ''
    resp.form['object_id'] = formdata2.id
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 1

    resp.form['object_id'] = 'XXX'
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 0

    # check data format control
    assert app.get('/backoffice/journal/?object=plop').pyquery('tbody tr').length == 0
    assert app.get('/backoffice/journal/?date=plop').pyquery('tbody tr').length == 0
    app.get('/backoffice/journal/?min=plop', status=400)
    app.get('/backoffice/journal/?max=plop', status=400)

    # check journal is still displayed correctly after formdef removal
    resp.form['object_id'] = ''
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 2
    assert resp.pyquery('.journal-table--description')[-1].text == 'CSV Export - form title 2'
    formdef2.remove_self()
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 2
    assert resp.pyquery('.journal-table--description')[-1].text == 'CSV Export - form title 2'

    # check settings entry
    resp.form['object'] = ''
    resp.form['object_id'] = ''
    resp.form['action'] = 'settings'
    resp = resp.form.submit('submit')
    assert resp.pyquery('tbody tr').length == 1
    assert (
        resp.pyquery('tbody td.journal-table--description').text() == 'Change to global settings - filetypes'
    )


def test_audit_journal_remote_access(pub, superuser):
    app = login(get_app(pub))
    resp = app.get('/backoffice/journal/')
    assert 'Redirect to remote stored file' not in [x[2] for x in resp.form['action'].options]

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.add_section('storage-remote')
    pub.site_options.set('storage-remote', 'label', 'remote')
    pub.site_options.set('storage-remote', 'class', 'wcs.qommon.upload_storage.RemoteOpaqueUploadStorage')
    pub.site_options.set('storage-remote', 'ws', 'https://crypto.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/backoffice/journal/')
    assert 'Redirect to remote stored file' in [x[2] for x in resp.form['action'].options]


def test_audit_journal_access(pub, superuser):
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()

    app = login(get_app(pub))
    assert '/journal/' in app.get('/backoffice/studio/')
    app.get('/backoffice/journal/', status=200)
    pub.cfg['admin-permissions'] = {'journal': [role.id]}
    pub.write_cfg()
    assert '/journal/' not in app.get('/backoffice/studio/')
    app.get('/backoffice/journal/', status=403)

    superuser.roles = [role.id]
    superuser.store()
    assert '/journal/' in app.get('/backoffice/studio/')
    app.get('/backoffice/journal/', status=200)

    superuser.is_admin = False
    superuser.store()
    app.get('/backoffice/journal/', status=200)
