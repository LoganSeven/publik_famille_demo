import os
import re

import pytest

from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_environment, create_superuser, create_user


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
    [api-secrets]
    coucou = 1234
    [options]
    disable-internal-statistics = false
    '''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_statistics_with_no_formdefs(pub):
    create_user(pub)
    create_environment(pub)
    FormDef.wipe()
    from wcs.sql import drop_global_views, get_connection_and_cursor

    conn, cur = get_connection_and_cursor()
    drop_global_views(conn, cur)
    cur.close()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/statistics')
    assert 'This site is currently empty.' in resp


def test_backoffice_statistics_feature_flag(pub):
    create_superuser(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'Statistics' in resp.text

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')

    pub.site_options.set('options', 'disable-internal-statistics', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/management/form-title/')
    assert 'Statistics' not in resp.text


def test_backoffice_statistics_status_filter(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Statistics')
    # status filter is not displayed by default but an hidden field is set.
    assert resp.forms['listing-settings']['filter'].attrs['type'] == 'hidden'

    # add 'status' as a filter
    resp.forms['listing-settings']['filter-status'].checked = True
    resp = resp.forms['listing-settings'].submit()
    assert resp.forms['listing-settings']['filter'].tag == 'select'

    assert resp.forms['listing-settings']['filter'].value == 'all'
    resp.forms['listing-settings']['filter'].value = 'pending'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 17' in resp.text
    resp.forms['listing-settings']['filter'].value = 'waiting'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 17' in resp.text

    resp.forms['listing-settings']['filter'].value = 'done'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 33' in resp.text

    resp.forms['listing-settings']['filter'].value = 'rejected'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 0' in resp.text

    resp.forms['listing-settings']['filter'].value = 'all'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 50' in resp.text


def test_backoffice_statistics_status_select(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    formdef = FormDef.get_by_urlname('form-title')
    field1 = formdef.fields[1]
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Statistics')
    assert 'filter-%s-value' % field1.id not in resp.form.fields

    resp.forms['listing-settings']['filter-%s' % field1.id].checked = True
    resp = resp.forms['listing-settings'].submit()
    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'bar'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 13' in resp.text

    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'baz'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 24' in resp.text
    assert resp.pyquery('ul.resolution-times.status-wf-new li')[0].text == 'Count: 8'

    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'baz'
    resp.forms['listing-settings']['filter-%s-operator' % field1.id].value = 'in'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 24' in resp.text
    assert resp.pyquery('ul.resolution-times.status-wf-new li')[0].text == 'Count: 8'

    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'baz'
    resp.forms['listing-settings']['filter-%s-operator' % field1.id].value = 'not_in'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 26' in resp.text
    assert resp.pyquery('ul.resolution-times.status-wf-new li')[0].text == 'Count: 9'

    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'baz'
    resp.forms['listing-settings']['filter-%s-operator' % field1.id].value = 'existing'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 50' in resp.text
    assert resp.pyquery('ul.resolution-times.status-wf-new li')[0].text == 'Count: 17'

    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = 'foo'
    resp.forms['listing-settings']['filter-%s-operator' % field1.id].value = 'eq'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 13' in resp.text

    # check it's also possible to get back to the complete list
    resp.forms['listing-settings']['filter-%s-value' % field1.id].value = ''
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 50' in resp.text

    # check it also works with item fields with a data source
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Statistics')
    resp.forms['listing-settings']['filter-3'].checked = True
    resp = resp.forms['listing-settings'].submit()
    resp.forms['listing-settings']['filter-3-value'].value = 'A'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 13' in resp.text

    # set field to be displayed by default in filters
    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields[1].in_filters = True
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Statistics')
    assert 'filter-%s-value' % field1.id in resp.form.fields


def test_backoffice_statistics_custom_view_ieq(pub):
    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom test view'
    custom_view.formdef = formdef
    custom_view.visibility = 'any'
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/custom-test-view/stats')
    assert 'Total number of records: 50' in resp.text

    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'FOO BAR 0'}
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/custom-test-view/stats')
    assert 'Total number of records: 1' in resp.text

    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'foo bar 0'}
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/custom-test-view/stats')
    assert 'Total number of records: 0' in resp.text

    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'foo bar 0', 'filter-1-operator': 'ieq'}
    custom_view.store()
    resp = app.get('/backoffice/management/form-title/custom-test-view/stats')
    assert 'Total number of records: 1' in resp.text


def test_global_statistics(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    resp = resp.click('Global statistics')
    assert 'Total count: 70' in resp.text

    resp.forms[0]['start'] = '2014-01-01'
    resp.forms[0]['end'] = '2014-12-31'
    resp = resp.forms[0].submit()
    assert 'Total count: 20' in resp.text

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')

    pub.site_options.set('options', 'disable-internal-statistics', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get('/backoffice/management/forms')
    assert 'Global statistics' not in resp


def test_backoffice_statistics(pub):
    create_superuser(pub)
    create_environment(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Statistics')
    assert 'Total number of records: 50' in resp.text
    assert 'New: 17' in resp.text
    assert 'Finished: 33' in resp.text
    assert re.findall('foo.*26.*bar.*26.*bar.*48', resp.text)  # percentages
    assert 'Resolution time' in resp.text
    assert 'To Status &quot;New&quot;' in resp.text
    assert 'To Status &quot;Finished&quot;' in resp.text

    resp.forms['listing-settings']['filter-end-value'] = '2013-01-01'
    resp = resp.forms['listing-settings'].submit()
    assert 'Total number of records: 0' in resp.text
