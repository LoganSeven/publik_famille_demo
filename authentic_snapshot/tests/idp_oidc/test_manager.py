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

import pytest
from django.urls import reverse
from webtest import Upload

from authentic2.apps.journal.models import Event
from authentic2_idp_oidc import app_settings as oidc_app_settings
from authentic2_idp_oidc.models import OIDCClaim, OIDCClient
from tests.utils import login


@pytest.fixture
def app(app, admin):
    login(app, admin)
    return app


@pytest.fixture
def superuser_app(app, superuser):
    login(app, superuser)
    return app


def test_add_oidc_service_superuser(superuser_app):
    resp = superuser_app.get('/manage/services/')
    assert 'Add OIDC service' in resp.text
    assert OIDCClient.objects.count() == 0
    assert OIDCClaim.objects.count() == 0

    Event.objects.all().delete()
    service_name = 'Test'

    resp = resp.click('Add OIDC service')
    form = resp.form
    form['name'] = service_name
    form['redirect_uris'] = 'http://example.com'
    form['has_api_access'] = True
    form['activate_user_profiles'] = True
    resp = form.submit()

    assert OIDCClient.objects.count() == 1
    assert OIDCClaim.objects.count() == len(oidc_app_settings.DEFAULT_MAPPINGS)
    oidc_client = OIDCClient.objects.get()
    assert oidc_client.has_api_access is True
    assert oidc_client.activate_user_profiles is True
    assert resp.location == f'/manage/services/{oidc_client.pk}/'
    resp = resp.follow()
    assert 'Settings' in resp.text
    assert 'Delete' in resp.text

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1
    assert evts[0].message == f'creation of OIDCClient "{service_name}"'
    assert evts[0].type.name == 'manager.service.creation'


def test_add_oidc_service_admin(app):
    resp = app.get('/manage/services/')
    assert 'Add OIDC service' in resp.text
    assert OIDCClient.objects.count() == 0
    assert OIDCClaim.objects.count() == 0

    Event.objects.all().delete()
    service_name = 'Test'

    resp = resp.click('Add OIDC service')
    form = resp.form
    form['name'] = 'Test'
    form['redirect_uris'] = 'http://example.com'
    form['uses_refresh_tokens'] = True
    assert 'has_api_access' not in form.fields
    assert 'activate_user_profiles' not in form.fields
    resp = form.submit()

    assert OIDCClient.objects.count() == 1
    assert OIDCClaim.objects.count() == len(oidc_app_settings.DEFAULT_MAPPINGS)
    oidc_client = OIDCClient.objects.get()
    assert oidc_client.has_api_access is False
    assert oidc_client.activate_user_profiles is False
    assert oidc_client.uses_refresh_tokens is True
    assert 'offline_access' in oidc_client.scope_set()
    assert resp.location == f'/manage/services/{oidc_client.pk}/'
    resp = resp.follow()
    assert 'Settings' in resp.text
    assert 'Delete' in resp.text

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1
    assert evts[0].message == f'creation of OIDCClient "{service_name}"'
    assert evts[0].type.name == 'manager.service.creation'


class TestEdit:
    @pytest.fixture(autouse=True)
    def oidc_client(self, db):
        return OIDCClient.objects.create(name='Test', slug='test', redirect_uris='http://example.com')

    def test_edit(self, app, oidc_client):
        resp = app.get('/manage/services/')
        Event.objects.all().delete()
        resp = resp.click('Test')
        resp = resp.click('Settings')
        assert resp.pyquery('.service-field--value')
        for value in resp.pyquery('.service-field--value'):
            assert '\n' not in value.text
            assert not value.text.endswith(' ')
        resp = resp.click('Edit')

        # check breadcrumbs
        crumbs = [crumb for crumb in resp.pyquery('#breadcrumb')[0]]
        assert len(crumbs) == 6

        assert crumbs[0].text == 'Homepage'
        assert crumbs[0].items() == [('href', '/')]

        assert crumbs[1].text == 'Administration'
        assert crumbs[1].items() == [('href', '/manage/')]

        assert crumbs[2].text == 'Services'
        assert crumbs[2].items() == [('href', '/manage/services/')]

        assert crumbs[3].text == 'Test'
        assert crumbs[3].items() == [('href', f'/manage/services/{oidc_client.id}/')]

        assert crumbs[4].text == 'Configuration'
        assert crumbs[4].items() == [('href', f'/manage/services/{oidc_client.id}/settings/')]

        assert not crumbs[5].text
        assert crumbs[5].items() == [('href', '#')]

        form = resp.form
        form['name'] = 'New Test'
        form['colour'] = '#ff00ff'
        form['logo'] = Upload('tests/201x201.jpg')
        form['uses_refresh_tokens'] = True
        resp = form.submit()
        assert resp.location == '..'
        resp = resp.follow()
        assert 'New Test' in resp.text
        assert '#ff00ff' in resp.text
        assert '201x201.jpg' in resp.text
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 4
        assert evts[0].type.name == 'manager.service.edit'
        assert evts[0].type.name == evts[1].type.name
        assert evts[0].type.name == evts[2].type.name
        assert {ev.message for ev in evts} == {
            'OIDCClient "New Test" : changing name from "Test" to "New Test"',
            'OIDCClient "New Test" : adding colour with value "#ff00ff"',
            'OIDCClient "New Test" : adding logo with value "201x201.jpg \
(e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855)"',
            'OIDCClient "New Test" : changing uses_refresh_tokens from \
"False" to "True"',
        }
        Event.objects.all().delete()
        resp = resp.click('Edit')
        form = resp.form
        form['logo-clear'] = True
        resp = form.submit()
        assert resp.location == '..'
        resp = resp.follow()
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        evt = evts[0]
        assert evt.type.name == 'manager.service.edit'
        assert (
            evt.message
            == 'OIDCClient "New Test" : removing logo with value \
"201x201.jpg (b24c76be289979517e812727338747d21b9a647fea13ef2cc7c7f890dcc05656)"'
        )

        old_id = oidc_client.sector_identifier_uri
        oidc_client.refresh_from_db()
        oidc_client.sector_identifier_uri = 'https://thisisnotavaliduri/'
        assert oidc_client.uses_refresh_tokens is True
        assert 'offline_access' in oidc_client.scope_set()
        oidc_client.save()
        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form['name'] = 'Foo'
        resp = form.submit()
        assert resp.pyquery('.error').text() == 'Enter a valid URL.'
        oidc_client.sector_identifier_uri = old_id
        oidc_client.save()

        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        assert resp.pyquery.find('a.generate-input-value[aria-controls="id_client_id"]')
        assert form.fields['client_id'][0].attrs['readonly'] == 'readonly'
        assert resp.pyquery.find('a.generate-input-value[aria-controls="id_client_secret"]')
        assert form.fields['client_secret'][0].attrs['readonly'] == 'readonly'

        form.set('authorization_mode', str(OIDCClient.AUTHORIZATION_MODE_NONE))
        form.set('always_save_authorization', True)

        resp = form.submit()
        assert 'errors processing your form' in resp.pyquery('.errornotice p')[0].text
        assert (
            'Cannot save user authorizations when authorization mode is none.'
            in resp.pyquery('.error p')[0].text
        )
        oidc_client.refresh_from_db()
        assert oidc_client.authorization_mode != OIDCClient.AUTHORIZATION_MODE_NONE
        assert not oidc_client.always_save_authorization
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

        form = resp.form
        form.set('authorization_mode', str(OIDCClient.AUTHORIZATION_MODE_BY_SERVICE))
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert (
            evts[0].message
            == 'OIDCClient "New Test" : changing always_save_authorization from "False" to "True"'
        )
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form[
            'redirect_uris'
        ] = '''http://example.com
http://example2.com'''
        form['sector_identifier_uri'] = ''
        resp = form.submit()
        assert 'errors processing your form' in resp.pyquery('.errornotice p')[0].text
        assert (
            'Cannot save redirect URIs bearing different domains if no sector identifier URI is provided.'
            in resp.pyquery('.error p')[0].text
        )
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

        form = resp.form
        form[
            'redirect_uris'
        ] = '''http://example.com
https://example.com/auth
http://example.com/misc/auth2'''
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert (
            evts[0].message
            == 'OIDCClient "New Test" : \
changing redirect_uris from "http://example.com" to \
"http://example.com\nhttps://example.com/auth\nhttp://example.com/misc/auth2"'
        )
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form[
            'redirect_uris'
        ] = '''http://example.com
http://example2.com'''
        form['sector_identifier_uri'] = 'example.com'
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 2
        assert evts[0].type.name == 'manager.service.edit'
        assert evts[0].type.name == evts[1].type.name
        assert {ev.message for ev in evts} == {
            'OIDCClient "New Test" : \
changing redirect_uris from \
"http://example.com\nhttps://example.com/auth\nhttp://example.com/misc/auth2" to \
"http://example.com\nhttp://example2.com"',
            'OIDCClient "New Test" : changing sector_identifier_uri from "" to "http://example.com"',
        }
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form['sector_identifier_uri'] = 'example2.com'
        resp = form.submit()
        assert (
            'You are not allowed to set an URI that does not match "example.com" '
            'because this value is used by the identifier policy.'
        ) == resp.pyquery('.error p').text()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form['slug'] = 'anewslug'
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        oidc_client.refresh_from_db()
        assert oidc_client.slug == 'anewslug'
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert evts[0].message == 'OIDCClient "New Test" : changing slug from "test" to "anewslug"'
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form['slug'] = 'anew Invalid! slug'
        resp = form.submit()
        assert 'errors processing your form' in resp.pyquery('.errornotice p')[0].text
        assert (
            'Enter a valid “slug” consisting of letters, numbers, underscores or hyphens.'
            in resp.pyquery('.error p')[0].text
        )
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form['client_id'] = 'superid'
        form['client_secret'] = 'hackme'
        resp = form.submit()
        assert resp.pyquery('.errornotice p')
        assert {elt.text.strip() for elt in resp.pyquery('.error p')} == {
            'Please use the generate link to change client_secret',
            'Please use the generate link to change client_id',
        }
        oidc_client.refresh_from_db()
        assert oidc_client.client_id != 'superid'
        assert oidc_client.client_secret != 'hackme'
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        old_secret = oidc_client.client_secret
        form['client_secret'] = new_secret = str(uuid.uuid4())
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        oidc_client.refresh_from_db()
        assert oidc_client.client_secret == new_secret
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert (
            evts[0].message
            == f'OIDCClient "New Test" : changing client_secret from "{old_secret}" to "xxxNEW_SECRETxxx"'
        )
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        old_id = oidc_client.client_id
        form['client_id'] = new_id = str(uuid.uuid4())
        resp = form.submit().follow()
        assert not resp.pyquery('.errornotice p')
        assert not resp.pyquery('.error p')
        oidc_client.refresh_from_db()
        assert oidc_client.client_id == new_id
        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert (
            evts[0].message
            == f'OIDCClient "New Test" : changing client_id from "{old_id}" to "xxxNEW_SECRETxxx"'
        )
        Event.objects.all().delete()

        resp = app.get(f'/manage/services/{oidc_client.id}/settings/edit/')
        form = resp.form
        form.submit().follow()
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

    def test_delete(self, app):
        resp = app.get('/manage/services/')
        Event.objects.all().delete()

        resp = resp.click('Test')
        resp = resp.click('Delete')
        resp = resp.form.submit().follow()
        assert OIDCClient.objects.count() == 0

        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].message == 'deletion of OIDCClient "Test"'
        assert evts[0].type.name == 'manager.service.deletion'

    def test_add_claim(self, app, oidc_client):
        Event.objects.all().delete()
        resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
        resp = resp.click('Add claim')
        form = resp.form
        form['name'] = 'claim'
        form['value'] = 'value'
        form['scopes'] = 'profile'
        resp = form.submit()
        assert resp.location == f'/manage/services/{oidc_client.pk}/settings/#oidc-claims'
        assert OIDCClaim.objects.filter(
            client=oidc_client, name='claim', value='value', scopes='profile'
        ).exists()

        evts = list(Event.objects.order_by('timestamp', 'id'))
        assert len(evts) == 1
        assert evts[0].type.name == 'manager.service.edit'
        assert (
            evts[0].message
            == "OIDCClient \"Test\" : adding OIDC claim with value \"{'name': 'claim', 'value': 'value', 'scopes': 'profile'}\""
        )

    def test_add_claim_mandatory_field_name(self, app, oidc_client):
        Event.objects.all().delete()
        resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
        resp = resp.click('Add claim')
        assert (
            resp.pyquery('#help_text_id_value').text()
            == 'Use “⇩” (arrow down) for pre-defined claim values from the user profile.'
        )
        form = resp.form
        form['value'] = 'value'
        form['scopes'] = 'profile'
        resp = form.submit()
        assert len(resp.pyquery('.error')) == 1
        assert 'This field is required.' in resp.pyquery('.error').text()
        assert not OIDCClaim.objects.filter(client=oidc_client, name='claim', value='value', scopes='profile')
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

    def test_add_claim_mandatory_field_value(self, app, oidc_client):
        Event.objects.all().delete()
        resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
        resp = resp.click('Add claim')
        form = resp.form
        form['name'] = 'claim'
        form['scopes'] = 'profile'
        resp = form.submit()
        assert len(resp.pyquery('.error')) == 1
        assert 'This field is required.' in resp.pyquery('.error').text()
        assert not OIDCClaim.objects.filter(client=oidc_client, name='claim', value='value', scopes='profile')
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

    def test_add_claim_mandatory_field_scope(self, app, oidc_client):
        Event.objects.all().delete()
        resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
        resp = resp.click('Add claim')
        form = resp.form
        form['name'] = 'claim'
        form['value'] = 'value'
        resp = form.submit()
        assert len(resp.pyquery('.error')) == 1
        assert 'This field is required.' in resp.pyquery('.error').text()
        assert not OIDCClaim.objects.filter(client=oidc_client, name='claim', value='value', scopes='profile')
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

    def test_add_claim_redundancy_error(self, app, oidc_client):
        Event.objects.all().delete()
        resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
        OIDCClaim.objects.create(
            client=oidc_client, name='yet_another_claim', value='value', scopes='profile'
        )

        resp = resp.click('Add claim')
        form = resp.form
        form['name'] = 'yet_another_claim'
        form['value'] = 'value'
        form['scopes'] = 'profile'
        resp = form.submit()
        assert len(resp.pyquery('.error')) == 1
        assert 'claim name is already defined for this client' in resp.pyquery('.error').text()
        assert (
            OIDCClaim.objects.filter(
                client=oidc_client, name='yet_another_claim', value='value', scopes='profile'
            ).count()
            == 1
        )
        assert len(list(Event.objects.order_by('timestamp', 'id'))) == 0

    class TestEditClaim:
        @pytest.fixture(autouse=True)
        def claim(self, oidc_client):
            return OIDCClaim.objects.create(client=oidc_client, name='claim', value='value', scopes='profile')

        def test_edit_claim(self, app, oidc_client, claim):
            Event.objects.all().delete()
            resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
            assert 'claim' in resp.text
            resp = resp.click('Edit', index=1)
            assert (
                resp.pyquery('#help_text_id_value').text()
                == 'Use “⇩” (arrow down) for pre-defined claim values from the user profile.'
            )
            form = resp.form
            form['value'] = 'new value'
            resp = form.submit()
            assert resp.location == f'/manage/services/{oidc_client.pk}/settings/#oidc-claims'
            claim.refresh_from_db()
            assert claim.value == 'new value'

            evts = list(Event.objects.order_by('timestamp', 'id'))
            assert len(evts) == 1
            assert evts[0].type.name == 'manager.service.edit'
            assert (
                evts[0].message
                == "OIDCClient \"Test\" : changing OIDC claim from \"{'value': 'value'}\" to \"{'value': 'new value'}\""
            )

        def test_delete_claim(self, app, oidc_client):
            Event.objects.all().delete()
            resp = app.get(f'/manage/services/{oidc_client.pk}/settings/')
            assert 'claim' in resp.text
            resp = resp.click('Delete')
            form = resp.form
            resp = form.submit()
            assert resp.location == f'/manage/services/{oidc_client.pk}/settings/#oidc-claims'
            assert OIDCClaim.objects.filter(client=oidc_client).count() == 0

            evts = list(Event.objects.order_by('timestamp', 'id'))
            assert len(evts) == 1
            assert evts[0].type.name == 'manager.service.edit'
            assert (
                evts[0].message
                == "OIDCClient \"Test\" : removing OIDC claim with value \"{'name': 'claim', 'value': 'value', 'scopes': 'profile'}\""
            )


def test_uuid_generation(superuser_app):
    resp = superuser_app.get(reverse('a2-manager-service-generate-uuid'))

    data = json.loads(resp.text)

    assert 'uuid' in data
    uuid1 = data['uuid']
    assert uuid.UUID(data['uuid'])

    resp = superuser_app.get(reverse('a2-manager-service-generate-uuid'))
    data = json.loads(resp.text)
    assert 'uuid' in data
    uuid2 = data['uuid']
    assert uuid.UUID(data['uuid'])
    assert uuid2 != uuid1
