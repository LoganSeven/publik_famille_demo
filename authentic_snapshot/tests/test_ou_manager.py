# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import uuid

from webtest import Upload

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou

from .utils import login


def test_manager_ou_export(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-ous')

    export_response = response.click('Export')
    export = export_response.json

    assert list(export.keys()) == ['ous']
    assert len(export['ous']) == 3
    assert {ou['slug'] for ou in export['ous']} == {'default', 'ou1', 'ou2'}

    response.form.set('search-text', 'ou1')
    search_response = response.form.submit()

    export_response = search_response.click('Export')
    export = export_response.json

    assert len(export['ous']) == 1
    assert export['ous'][0]['slug'] == 'ou1'


def test_manager_ou_import(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-ous')

    export_response = response.click('Export')
    export = export_response.json

    assert len(export['ous']) == 3
    assert not 'roles' in export
    ou1.delete()
    ou2.delete()

    resp = app.get('/manage/organizational-units/')
    resp = resp.click('Import')
    assert 'Organizational Units Export File' in resp.text
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    assert OrganizationalUnit.objects.filter(name=ou1.name).exists()
    assert OrganizationalUnit.objects.filter(name=ou2.name).exists()

    export_response = response.click('Export')
    new_export = export_response.json
    assert len(export['ous']) == 3
    assert new_export['ous'][1]['uuid'] == export['ous'][1]['uuid']
    assert new_export['ous'][2]['uuid'] == export['ous'][2]['uuid']

    # in case roles are present in export file, they must not be imported
    export['roles'] = [
        {
            'uuid': '27255f404cb140df9a577da76b59f285',
            'slug': 'should_not_exist',
            'name': 'should_not_exist',
        }
    ]
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    assert not Role.objects.filter(slug='should_not_exist').exists()


def test_manager_ou_import_defaultness_variations(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-ous')

    export_response = response.click('Export')
    export = export_response.json
    for ou in export['ous']:
        if ou['slug'] == 'default':
            ou['slug'] = ou['name'] = 'citizens'  # change object's natural key
            ou['uuid'] = uuid.uuid4().hex  # prevent matching on uuid

    ou1.delete()
    ou2.delete()

    resp = app.get('/manage/organizational-units/')
    resp = resp.click('Import')
    assert 'Organizational Units Export File' in resp.text
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    assert resp.pyquery('li.success').text() == 'Organizational Units have been successfully imported.'
    assert resp.pyquery('li.warning').text() == "New organizational unit citizens can't be set as default."

    assert OrganizationalUnit.objects.count() == 4
    assert not OrganizationalUnit.objects.get(slug='citizens').default
    assert get_default_ou() != OrganizationalUnit.objects.get(slug='citizens')


def test_ou_edit_form_local_options_overridden(app, admin, ou1, settings, phone_activated_authn):
    login(app, admin, 'a2-manager-ou-add')

    settings.A2_EMAIL_IS_UNIQUE = False
    settings.A2_USERNAME_IS_UNIQUE = True
    settings.A2_PHONE_IS_UNIQUE = True

    response = app.get('/manage/organizational-units/%s/edit/' % ou1.pk)

    assert 'disabled' not in response.pyquery('input#id_email_is_unique')[0].attrib
    assert response.pyquery('input#id_username_is_unique')[0].attrib['disabled']
    assert response.pyquery('input#id_phone_is_unique')[0].attrib['disabled']

    settings.A2_EMAIL_IS_UNIQUE = True
    settings.A2_USERNAME_IS_UNIQUE = False
    settings.A2_PHONE_IS_UNIQUE = False

    response = app.get('/manage/organizational-units/%s/edit/' % ou1.pk)

    assert response.pyquery('input#id_email_is_unique')[0].attrib['disabled']
    assert 'disabled' not in response.pyquery('input#id_username_is_unique')[0].attrib
    assert 'disabled' not in response.pyquery('input#id_phone_is_unique')[0].attrib

    assert not ou1.phone_is_unique
    assert not response.form['phone_is_unique'].value
    response.form.set('phone_is_unique', True)
    response.form.submit().follow()
    ou1.refresh_from_db()
    assert ou1.phone_is_unique
