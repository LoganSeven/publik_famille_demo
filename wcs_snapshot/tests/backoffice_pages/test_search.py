import re

import pytest

from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_studio_search(pub):
    FormDef.wipe()
    Workflow.wipe()

    for title in ('test', 'Test', 'Another test', 'Something else'):
        Workflow(name=title).store()

    user = create_user(pub)

    # allow limited access
    pub.cfg['admin-permissions'] = {'cards': user.roles}
    pub.write_cfg()

    user = create_user(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert not resp.pyquery('#studio-search-button')
    app.get('/backoffice/studio/search', status=403)

    # allow global access
    pub.cfg['admin-permissions'] = {'cards': user.roles, 'forms': user.roles, 'workflows': user.roles}
    pub.write_cfg()

    resp = app.get('/backoffice/studio/')
    assert resp.pyquery('#studio-search-button')
    resp = app.get('/backoffice/studio/search')
    resp.form['q'] = 'test'
    resp = resp.form.submit()
    assert resp.form['q'].value == 'test'
    ajax_resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert re.findall('href="(.*?)"', ajax_resp.text) == [
        'http://example.net/backoffice/workflows/1/',
        'http://example.net/backoffice/workflows/2/',
        'http://example.net/backoffice/workflows/3/',
    ]

    resp.form['q'] = 'other test'
    resp = resp.form.submit()
    ajax_resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert re.findall('href="(.*?)"', ajax_resp.text) == ['http://example.net/backoffice/workflows/3/']

    resp.form['q'] = 'xyz'
    resp = resp.form.submit()
    ajax_resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert 'Nothing found.' in ajax_resp.text

    for i in range(50):
        Workflow(name=f'test {i}').store()

    resp.form['q'] = 'test'
    resp = resp.form.submit()
    ajax_resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert len(re.findall('href="(.*?)"', ajax_resp.text)) == 50
    assert 'list-item-too-many' in ajax_resp.text

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [StringField(id=0, label='string field')]
    formdef.store()

    resp = app.get('/backoffice/studio/search')
    resp.form['q'] = 'string'
    resp = resp.form.submit()
    ajax_resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert len(re.findall('href="(.*?)"', ajax_resp.text)) == 1
    assert ajax_resp.pyquery.text() == 'test form, field: "string field" (Text (line))'
