# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

import pytest
import responses
from django.core.files.base import ContentFile
from django.utils.html import escape
from webtest import Upload

from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import AddRoleAction
from authentic2.manager.utils import label_from_role
from authentic2.models import Attribute
from authentic2_auth_oidc.models import OIDCAccount, OIDCClaimMapping, OIDCProvider
from tests.utils import assert_event, login, request_select2

from .test_misc import oidc_provider, oidc_provider_jwkset  # pylint: disable=unused-import


@pytest.mark.freeze_time('2022-04-19 14:00')
@responses.activate
def test_authenticators_oidc(app, superuser, ou1, ou2, jwkset_url, kid_rsa):
    resp = login(app, superuser, path='/manage/authenticators/')

    resp = resp.click('Add new authenticator')
    resp.form['name'] = 'Test'
    resp.form['authenticator'] = 'oidc'
    resp = resp.form.submit()
    assert '/edit/' in resp.location
    assert_event('authenticator.creation', user=superuser, session=app.session)

    provider = OIDCProvider.objects.get(slug='test')
    resp = app.get(provider.get_absolute_url())
    assert 'extra-actions-menu-opener' in resp.text
    assert 'Creation date: April 19, 2022, 2 p.m.' in resp.text
    assert 'Last modification date: April 19, 2022, 2 p.m.' in resp.text
    assert 'Issuer' not in resp.text

    assert 'Enable' not in resp.text
    assert 'configuration is not complete' in resp.text
    app.get('/manage/authenticators/%s/toggle/' % provider.pk, status=403)

    resp = resp.click('Edit')
    assert 'enabled' not in resp.form.fields
    assert 'last_sync_time' not in resp.form.fields
    assert resp.pyquery('input#id_client_id').val() == ''
    assert resp.pyquery('input#id_client_secret').val() == ''
    resp.form['ou'] = ou1.pk
    resp.form['issuer'] = 'https://oidc.example.com'
    resp.form['scopes'] = 'profile email'
    resp.form['strategy'] = 'create'
    resp.form['authorization_endpoint'] = 'https://oidc.example.com/authorize'
    resp.form['token_endpoint'] = 'https://oidc.example.com/token'
    resp.form['userinfo_endpoint'] = 'https://oidc.example.com/user_info'
    resp.form['button_label'] = 'Test'
    resp.form['button_description'] = 'test'
    resp.form['client_id'] = 'auie'
    resp.form['client_secret'] = 'tsrn'
    resp.form['idtoken_algo'].select(text='RSA')
    resp.form['jwkset_url'] = jwkset_url
    resp = resp.form.submit().follow()
    assert_event('authenticator.edit', user=superuser, session=app.session)

    assert 'Issuer: https://oidc.example.com' in resp.text
    assert 'Scopes: profile email' in resp.text

    resp = app.get('/manage/authenticators/')
    assert 'OpenID Connect - Test' in resp.text
    assert 'class="section disabled"' in resp.text
    assert 'OIDC provider linked to' not in resp.text

    resp = resp.click('Configure', index=1)
    resp = resp.click('Enable').follow()
    assert 'Authenticator has been enabled.' in resp.text
    assert_event('authenticator.enable', user=superuser, session=app.session)

    resp = resp.click('Journal of edits')
    assert resp.pyquery('.journal-list--message-column:contains("creation")')
    assert resp.pyquery('.journal-list--message-column:contains("enable")')
    edit_message = resp.pyquery('.journal-list--message-column:contains("edit")').text()
    terms = {term.strip(',').strip('(').strip(')') for term in edit_message.split()}
    assert terms == {
        'edit',
        'ou',
        'issuer',
        'scopes',
        'strategy',
        'client_id',
        'button_label',
        'client_secret',
        'token_endpoint',
        'userinfo_endpoint',
        'button_description',
        'jwkset_url',
        'authorization_endpoint',
    }

    provider.refresh_from_db()
    provider.jwkset_url = jwkset_url
    provider.save()

    resp = app.get('/manage/authenticators/%s/edit/' % provider.pk)
    assert resp.pyquery('input#id_jwkset_url')[0].value == jwkset_url
    assert 'disabled' in resp.pyquery('textarea#id_jwkset_json')[0].keys()
    assert f'"kid": "{kid_rsa}"' in resp.pyquery('textarea#id_jwkset_json')[0].text
    assert (
        resp.pyquery('div[aria-labelledby="id_jwkset_json_title"] div.hint p')[0].text
        == 'JSON is fetched from the WebKey Set URL'
    )

    resp = app.get('/manage/authenticators/')
    assert 'class="section disabled"' not in resp.text
    assert 'OIDC provider linked to https://oidc.example.com with scopes profile, email.' not in resp.text

    # same name
    resp = resp.click('Add new authenticator')
    resp.form['name'] = 'test'
    resp.form['authenticator'] = 'oidc'
    resp = resp.form.submit().follow()
    assert OIDCProvider.objects.filter(slug='test-1').count() == 1
    OIDCProvider.objects.filter(slug='test-1').delete()

    # no name
    resp = app.get('/manage/authenticators/add/')
    resp.form['authenticator'] = 'oidc'
    resp = resp.form.submit()
    assert 'This field is required' in resp.text

    resp = app.get('/manage/authenticators/')
    resp = resp.click('Configure', index=1)
    resp = resp.click('Disable').follow()
    assert 'Authenticator has been disabled.' in resp.text
    assert_event('authenticator.disable', user=superuser, session=app.session)

    resp = app.get('/manage/authenticators/')
    assert 'class="section disabled"' in resp.text

    resp = resp.click('Configure', index=1)
    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert not OIDCProvider.objects.filter(slug='test').exists()
    assert_event('authenticator.deletion', user=superuser, session=app.session)


def test_authenticators_oidc_claims(app, superuser):
    authenticator = OIDCProvider.objects.create(slug='idp1')
    resp = login(app, superuser, path=authenticator.get_absolute_url())

    resp = resp.click('Add', href='claim')
    resp.form['claim'] = 'email'
    resp.form['attribute'].select(text='Email address (email)')
    resp.form['verified'].select(text='verified claim')
    resp.form['required'] = True
    resp.form['idtoken_claim'] = True
    resp = resp.form.submit()
    assert_event('authenticator.related_object.creation', user=superuser, session=app.session)
    assert '#open:oidcclaimmapping' in resp.location

    resp = resp.follow()
    assert 'email → Email address (email), verified, required, idtoken' in resp.text

    resp = resp.click('email')
    resp.form['attribute'].select(text='First name (first_name)')
    resp = resp.form.submit().follow()
    assert 'email → First name (first_name), verified, required, idtoken' in resp.text
    assert_event('authenticator.related_object.edit', user=superuser, session=app.session)

    resp = resp.click('Remove')
    resp = resp.form.submit().follow()
    assert 'email' not in resp.text
    assert_event('authenticator.related_object.deletion', user=superuser, session=app.session)


@responses.activate
def test_authenticators_oidc_hmac(app, superuser, ou1, ou2, kid_rsa):
    resp = login(app, superuser, path='/manage/authenticators/')

    resp = resp.click('Add new authenticator')
    resp.form['name'] = 'Test'
    resp.form['authenticator'] = 'oidc'
    resp = resp.form.submit()
    assert '/edit/' in resp.location

    provider = OIDCProvider.objects.get(slug='test')
    resp = app.get(provider.get_absolute_url())

    resp = resp.click('Edit')
    resp.form['ou'] = ou1.pk
    resp.form['issuer'] = 'https://oidc.example.com'
    resp.form['scopes'] = 'profile email'
    resp.form['strategy'] = 'create'
    resp.form['authorization_endpoint'] = 'https://oidc.example.com/authorize'
    resp.form['token_endpoint'] = 'https://oidc.example.com/token'
    resp.form['userinfo_endpoint'] = 'https://oidc.example.com/user_info'
    resp.form['button_label'] = 'Test'
    resp.form['button_description'] = 'test'
    resp.form['client_id'] = 'auie'
    resp.form['client_secret'] = 'tsrn'
    resp.form['idtoken_algo'].select(text='HMAC')
    resp = resp.form.submit().follow()
    assert_event('authenticator.edit', user=superuser, session=app.session)


def test_authenticators_oidc_claims_disabled_attribute(app, superuser):
    authenticator = OIDCProvider.objects.create(slug='idp1')
    attr = Attribute.objects.create(kind='string', name='test_attribute', label='Test attribute')

    resp = login(app, superuser, path=authenticator.get_absolute_url())
    resp = resp.click('Add', href='claim')
    assert resp.pyquery('select#id_attribute option[value=test_attribute]')

    attr.disabled = True
    attr.save()

    resp = app.get(authenticator.get_absolute_url())
    resp = resp.click('Add', href='claim')
    assert not resp.pyquery('select#id_attribute option[value=test_attribute]')


def test_authenticators_oidc_add_role(app, superuser, role_ou1):
    authenticator = OIDCProvider.objects.create(slug='idp1')
    resp = login(app, superuser, path=authenticator.get_absolute_url())

    resp = resp.click('Add', href='role')
    select2_json = request_select2(app, resp, term='role_ou1')
    assert len(select2_json['results']) == 1
    resp.form['role'].force_value(select2_json['results'][0]['id'])
    resp = resp.form.submit().follow()
    assert 'role_ou1' in resp.text


def test_authenticators_oidc_export(app, superuser, simple_role):
    with open('tests/200x200.jpg', 'rb') as img:
        img_content = ContentFile(img.read(), name='200x200.jpg')
    authenticator = OIDCProvider.objects.create(
        slug='idp1', order=42, ou=get_default_ou(), enabled=True, button_image=img_content
    )
    OIDCClaimMapping.objects.create(authenticator=authenticator, claim='test', attribute='hop')
    AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)

    resp = login(app, superuser, path=authenticator.get_absolute_url())
    export_resp = resp.click('Export')

    resp = app.get('/manage/authenticators/import/')
    resp.form['authenticator_json'] = Upload('export.json', export_resp.body, 'application/json')
    resp = resp.form.submit()
    assert '/authenticators/%s/' % authenticator.pk in resp.location

    resp = resp.follow()
    assert 'Authenticator has been updated.' in resp.text
    assert OIDCProvider.objects.count() == 1
    assert OIDCClaimMapping.objects.count() == 1
    assert AddRoleAction.objects.count() == 1

    OIDCProvider.objects.all().delete()
    OIDCClaimMapping.objects.all().delete()
    AddRoleAction.objects.all().delete()

    resp = app.get('/manage/authenticators/import/')
    resp.form['authenticator_json'] = Upload('export.json', export_resp.body, 'application/json')
    resp = resp.form.submit().follow()
    assert 'Authenticator has been created.' in resp.text

    authenticator = OIDCProvider.objects.get()
    assert authenticator.slug == 'idp1'
    assert authenticator.order == 1
    assert authenticator.ou == get_default_ou()
    assert authenticator.enabled is False
    assert OIDCClaimMapping.objects.filter(
        authenticator=authenticator, claim='test', attribute='hop'
    ).exists()
    assert AddRoleAction.objects.filter(authenticator=authenticator, role=simple_role).exists()


def test_authenticators_oidc_import_ok_with_image(app, superuser, simple_role):
    resp = login(app, superuser, path='/manage/authenticators/import/')

    with open('tests/200x200.jpg', 'rb') as img:
        button_image = ContentFile(img.read(), name='200x200.jpg')

    authenticator = OIDCProvider.objects.create(
        slug='idp1',
        order=42,
        ou=get_default_ou(),
        enabled=True,
        button_image=button_image,
    )

    export_resp = app.get('/manage/authenticators/%s/export/' % authenticator.pk)
    export = json.loads(export_resp.text)

    # "Duplicating" authenticator by reimporting it with a new slug
    export['slug'] = 'idp2'
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    new_authenticator = OIDCProvider.objects.get(slug='idp2')
    assert authenticator != new_authenticator
    content1 = authenticator.button_image.read()
    content2 = new_authenticator.button_image.read()
    # both image has the same content but are different files
    assert content1 == content2
    assert authenticator.button_image.name != new_authenticator.button_image.name


def test_authenticators_oidc_import_ok_without_image(app, superuser, simple_role):
    resp = login(app, superuser, path='/manage/authenticators/import/')

    authenticator = OIDCProvider.objects.create(slug='idp1', order=42, ou=get_default_ou(), enabled=True)

    export_resp = app.get('/manage/authenticators/%s/export/' % authenticator.pk)
    export = json.loads(export_resp.text)
    assert 'button_image' not in export

    # "Duplicating" authenticator by reimporting it with a new slug
    export['slug'] = 'idp2'
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    new_authenticator = OIDCProvider.objects.get(slug='idp2')
    assert authenticator != new_authenticator
    assert not authenticator.button_image
    assert not new_authenticator.button_image


def test_authenticators_oidc_import_errors(app, superuser, simple_role):
    resp = login(app, superuser, path='/manage/authenticators/import/')
    resp.form['authenticator_json'] = Upload('export.json', b'not-json', 'application/json')
    resp = resp.form.submit()
    assert 'File is not in the expected JSON format.' in resp.text

    resp.form['authenticator_json'] = Upload('export.json', b'{}', 'application/json')
    resp = resp.form.submit()
    assert escape('Missing "authenticator_type" key.') in resp.text

    resp.form['authenticator_json'] = Upload(
        'export.json', b'{"authenticator_type": "xxx"}', 'application/json'
    )
    resp = resp.form.submit()
    assert 'Invalid authenticator_type: xxx.' in resp.text

    resp.form['authenticator_json'] = Upload(
        'export.json', b'{"authenticator_type": "x.y"}', 'application/json'
    )
    resp = resp.form.submit()
    assert 'Unknown authenticator_type: x.y.' in resp.text

    authenticator = OIDCProvider.objects.create(slug='idp1', order=42, ou=get_default_ou(), enabled=True)
    AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)

    export_resp = app.get('/manage/authenticators/%s/export/' % authenticator.pk)

    export = json.loads(export_resp.text)
    del export['slug']
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert 'Missing slug.' in resp.text

    export = json.loads(export_resp.text)
    export['ou'] = {'slug': 'xxx'}
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape("Organization unit not found: {'slug': 'xxx'}.") in resp.text

    export = json.loads(export_resp.text)
    del export['related_objects'][0]['object_type']
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Missing "object_type" key.') in resp.text

    export = json.loads(export_resp.text)
    del export['related_objects'][0]['role']
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Missing "role" key in add role action.') in resp.text

    export = json.loads(export_resp.text)
    export['related_objects'][0]['role'] = {'slug': 'xxx'}
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape("Role not found: {'slug': 'xxx'}.") in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = 'toto'
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: expect an array [NAME, BASE64_CONTENT]') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = None
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: expect an array [NAME, BASE64_CONTENT]') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = '01'
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: invalid base64') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = [
        'toto.jpg',
        'aGVsbG8gd29ybGQh',
    ]
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: base64 content is not a valid image') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = [
        'toto.jpg',
        'Hello world !',
    ]
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: invalid base64') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = [
        'toto.jpg',
        'Hello world !==',
    ]
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert escape('Invalid button_image: base64 content is not a valid image') in resp.text

    export = json.loads(export_resp.text)
    export['button_image'] = [
        '../../../../../../../../tmp/toto.jpg',
        '''
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAAXNSR0IB2cksfwAAAARnQU1BAACx
jwv8YQUAAAAgY0hSTQAAeiYAAICEAAD6AAAAgOgAAHUwAADqYAAAOpgAABdwnLpRPAAAAAxJREFU
CNdj+P//PwAF/gL+3MxZ5wAAAABJRU5ErkJggg==''',
    ]
    resp.form['authenticator_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit(status=400)


def test_authenticators_add_role_actions(app, admin, simple_role, role_ou1):
    authenticator = OIDCProvider.objects.create(slug='idp1', ou=get_default_ou(), enabled=True)
    authenticator.save()
    action = AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)

    login(app, admin)
    resp = app.get(authenticator.get_absolute_url())
    assert resp.pyquery(
        f'a[href="/manage/authenticators/{authenticator.pk}/addroleaction/{action.pk}/edit/"]'
    ).text() == label_from_role(simple_role)

    resp = resp.click(href=f'/manage/authenticators/{authenticator.pk}/addroleaction/add/')
    select2_json = request_select2(app, resp, term='role_ou1')
    assert len(select2_json['results']) == 1
    resp.form['role'].force_value(select2_json['results'][0]['id'])
    resp.form['condition'] = '{% %}'
    resp = resp.form.submit()
    assert 'template syntax error: Could not parse the remainder:' in resp.text

    resp.form['role'] = role_ou1.id
    resp.form['condition'] = '"Admin" in attributes.groups'
    resp = resp.form.submit().follow()
    action = AddRoleAction.objects.get(
        authenticator=authenticator, role=role_ou1, condition='"Admin" in attributes.groups'
    )
    assert resp.pyquery(
        f'a[href="/manage/authenticators/{authenticator.pk}/addroleaction/{action.pk}/edit/"]'
    ).text() == '%s (depending on condition)' % label_from_role(role_ou1)


def test_authenticators_oidc_related_objects_permissions(app, simple_user, simple_role):
    authenticator = OIDCProvider.objects.create(slug='idp1', order=42, ou=get_default_ou(), enabled=True)
    authenticator.save()
    mapping = OIDCClaimMapping.objects.create(authenticator=authenticator, claim='test', attribute='hop')
    action = AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)
    simple_user.roles.add(simple_role.get_admin_role())  # grant user access to /manage/

    role = Role.objects.get(name='Manager of authenticators')

    login(app, simple_user, path='/')
    app.get(authenticator.get_absolute_url(), status=403)
    app.get(f'/manage/authenticators/{authenticator.pk}/oidcclaimmapping/{mapping.pk}/edit/', status=403)
    app.get(f'/manage/authenticators/{authenticator.pk}/addroleaction/{action.pk}/delete/', status=403)
    app.get(f'/manage/authenticators/{authenticator.pk}/addroleaction/add/', status=403)

    simple_user.roles.add(role)

    app.get(authenticator.get_absolute_url())
    app.get(f'/manage/authenticators/{authenticator.pk}/oidcclaimmapping/{mapping.pk}/edit/')
    app.get(f'/manage/authenticators/{authenticator.pk}/addroleaction/{action.pk}/delete/')
    app.get(f'/manage/authenticators/{authenticator.pk}/addroleaction/add/')


def test_manager_user_sidebar(app, superuser, simple_user, oidc_provider):
    login(app, superuser, '/manage/')
    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'OIDC' not in response

    OIDCAccount.objects.create(user=simple_user, provider=oidc_provider, sub='1234')

    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'OIDC' in response
    assert 'Server' in response
    assert '1234' in response
