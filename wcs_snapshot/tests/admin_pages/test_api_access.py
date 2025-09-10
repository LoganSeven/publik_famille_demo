# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import pytest

from wcs.qommon.http_request import HTTPRequest
from wcs.sql import ApiAccess, Role

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def api_access():
    ApiAccess.wipe(restart_sequence=True)
    obj = ApiAccess()
    obj.name = 'Jhon'
    obj.description = 'API key for Jhon'
    obj.access_identifier = 'jhon'
    obj.access_key = '12345'
    obj.store()
    return obj


def test_api_access_new(pub):
    create_superuser(pub)
    ApiAccess.wipe()
    app = login(get_app(pub))

    # go to the page and cancel
    resp = app.get('/backoffice/settings/api-access/')
    resp = resp.click('New API access')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/'

    # go to the page and add an API access
    resp = app.get('/backoffice/settings/api-access/')
    resp = resp.click('New API access')
    resp.form['name'] = 'a new API access'
    resp.form['description'] = 'description'
    resp.form['access_identifier'] = 'new_access'
    assert len(resp.form['access_key'].value) == 36
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/'
    resp = resp.follow()
    assert 'a new API access' in resp.text
    resp = resp.click('a new API access')
    assert 'API access - a new API access' in resp.text

    # check name unicity
    resp = app.get('/backoffice/settings/api-access/new')
    resp.form['name'] = 'a new API access'
    resp.form['access_identifier'] = 'changed'
    resp = resp.form.submit('submit')
    assert resp.html.find('div', {'class': 'error'}).text == 'This name is already used.'

    # check access_identifier unicity
    resp.form['name'] = 'new one'
    resp.form['access_identifier'] = 'new_access'
    resp = resp.form.submit('submit')
    assert resp.html.find('div', {'class': 'error'}).text == 'This value is already used.'


def test_api_access_view(pub, api_access):
    create_superuser(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/api-access/%s/' % api_access.id)
    assert '12345' in resp.text

    resp = app.get('/backoffice/settings/api-access/wrong-id/', status=404)


def test_api_access_edit(pub, api_access):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/api-access/1/')
    resp = resp.click(href='edit')
    assert resp.form['name'].value == 'Jhon'
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/1/'
    resp = resp.follow()
    resp = resp.click(href='edit')
    resp.form['name'] = 'Smith Robert'
    resp.form['description'] = 'bla bla bla'
    resp.form['access_identifier'] = 'smith2'
    resp.form['access_key'] = '5678'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/1/'
    resp = resp.follow()

    api_access = ApiAccess.get('1')
    assert api_access.name == 'Smith Robert'
    assert api_access.description == 'bla bla bla'
    assert api_access.access_identifier == 'smith2'
    assert api_access.access_key == '5678'

    # check name unicity
    resp = app.get('/backoffice/settings/api-access/new')
    resp.form['name'] = 'Jhon'
    resp.form['access_identifier'] = 'jhon'
    resp = resp.form.submit('submit')
    resp = app.get('/backoffice/settings/api-access/1/')
    resp = resp.click(href='edit')
    resp.form['name'] = 'Jhon'
    resp = resp.form.submit('submit')
    assert resp.html.find('div', {'class': 'error'}).text == 'This name is already used.'


def test_api_access_delete(pub, api_access):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/api-access/1/')
    resp = resp.click(href='delete')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/'

    resp = app.get('/backoffice/settings/api-access/1/')
    resp = resp.click(href='delete')
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/api-access/'
    assert ApiAccess.count() == 0


def test_api_access_roles(pub, api_access):
    create_superuser(pub)

    pub.role_class.wipe()
    role_a = pub.role_class(name='a')
    role_a.store()
    role_b = pub.role_class(name='b')
    role_b.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/api-access/1/')
    resp = resp.click(href='edit')
    resp.form['roles$element0'] = role_a.id
    resp = resp.form.submit('roles$add_element')
    resp.form['roles$element1'] = role_b.id
    resp = resp.form.submit('submit')

    api_access = ApiAccess.get(api_access.id)
    assert {x.id for x in api_access.get_roles()} == {role_a.id, role_b.id}


def test_api_access_disabled(pub):
    create_superuser(pub)
    ApiAccess.wipe()

    pub.cfg['idp'] = {'xxx': {'metadata_url': 'http://idp.example.net/idp/saml2/metadata'}}
    pub.write_cfg()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/api-access/')
    assert 'New API access' not in resp.text

    assert 'API accesses are now globally managed on the identity provider.' in resp.text
    assert resp.pyquery('.infonotice a.pk-button').attr.href == 'http://idp.example.net/manage/api-clients/'


def test_api_access_missing_role(pub, api_access):
    create_superuser(pub)

    api_access.roles = [Role(id='foobar')]
    api_access.store()
    app = login(get_app(pub))
    app.get('/backoffice/settings/api-access/')
