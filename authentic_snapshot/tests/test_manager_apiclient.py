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

from urllib.parse import urlparse

import pytest
from django.urls import reverse

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import APIClient, Attribute
from authentic2.passwords import generate_apiclient_password, validate_apiclient_password

from .utils import login


@pytest.fixture
def strong_password():
    return generate_apiclient_password()


class TestAuthorization:
    @pytest.fixture
    def app(self, app, user):
        login(app, user)
        return app

    @pytest.fixture
    def api_client(self, db, ou1):
        return APIClient.objects.create_user(
            name='foo',
            description='foo-description',
            identifier='foo-description',
            password='foo-password',
            ou=ou1,
        )

    class Mixin:
        status_code = -1
        existing_client_status_code = -1

        def test_list(self, app):
            app.get(reverse('a2-manager-api-clients'), status=self.status_code)

        def test_add(self, app):
            app.get(reverse('a2-manager-api-client-add'), status=self.status_code)

        def test_detail(self, app, api_client):
            app.get(
                reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}),
                status=self.existing_client_status_code,
            )

        def test_edit(self, app, api_client):
            app.get(
                reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}),
                status=self.existing_client_status_code,
            )

        def test_delete(self, app, api_client):
            app.get(
                reverse('a2-manager-api-client-delete', kwargs={'pk': api_client.pk}),
                status=self.existing_client_status_code,
            )

    class TestAuthorization(Mixin):
        status_code = 403
        existing_client_status_code = 404

        @pytest.fixture
        def user(self, simple_user):
            return simple_user

    class TestAuthorizationLocalAdminWrongOu(Mixin):
        status_code = 200
        existing_client_status_code = 404

        @pytest.fixture
        def user(self, admin_ou2):
            return admin_ou2

    class TestAuthorizationLocalAdminRightOu(Mixin):
        status_code = 200
        existing_client_status_code = 200

        @pytest.fixture
        def user(self, admin_ou1):
            return admin_ou1

    class TestAuthorizationAdmin(Mixin):
        status_code = 200
        existing_client_status_code = 200

        @pytest.fixture
        def user(self, simple_user):
            simple_user.roles.add(Role.objects.get(ou__isnull=True, slug='_a2-manager-of-api-clients'))
            return simple_user


def test_list_empty(superuser, app):
    resp = login(app, superuser, 'a2-manager-api-clients')
    assert 'There are no API client defined.' in resp.text


def test_list_add_button(superuser, app):
    resp = login(app, superuser, 'a2-manager-api-clients')
    anchor = resp.pyquery('span.actions a[href="%s"]' % reverse('a2-manager-api-client-add'))
    assert anchor.text() == 'Add new API client'


def test_list_show_objects(superuser, app):
    api_client = APIClient.objects.create_user(
        name='foo', description='foo-description', identifier='foo-description', password='foo-password'
    )
    url = '/manage/api-clients/%s/' % api_client.pk
    resp = login(app, superuser, 'a2-manager-api-clients')
    anchor = resp.pyquery('div.content ul.objects-list a[href="%s"]' % url)
    assert anchor.text() == 'foo (foo-description) - Default organizational unit'


def test_list_show_objects_local_admin(admin_ou1, app, ou1, ou2):
    api_client_ou1 = APIClient.objects.create_user(
        name='foo',
        description='foo-description',
        identifier='foo-description',
        password='foo-password',
        ou=ou1,
    )
    api_client_ou2 = APIClient.objects.create_user(
        name='bar',
        description='bar-description',
        identifier='bar-description',
        password='bar-password',
        ou=ou2,
    )
    del api_client_ou1.password
    del api_client_ou2.password
    assert api_client_ou1.password != 'foo-password'
    assert api_client_ou1.check_password('foo-password')
    assert api_client_ou2.password != 'bar-password'
    assert api_client_ou2.check_password('bar-password')
    url = '/manage/api-clients/%s/' % api_client_ou1.pk
    resp = login(app, admin_ou1, 'a2-manager-api-clients')
    assert len(resp.pyquery('div.content ul.objects-list li')) == 1
    anchor = resp.pyquery('div.content ul.objects-list a[href="%s"]' % url)
    assert anchor.text() == 'foo (foo-description) - OU1'

    role = Role.objects.get(slug='_a2-manager-of-api-clients-%s' % ou2.slug)
    admin_ou1.roles.add(role)
    admin_ou1.save()
    resp = app.get(reverse('a2-manager-api-clients'))
    assert len(resp.pyquery('div.content ul.objects-list li')) == 2
    anchor = resp.pyquery('div.content ul.objects-list a[href="%s"]' % url)
    assert anchor.text() == 'foo (foo-description) - OU1'
    url = '/manage/api-clients/%s/' % api_client_ou2.pk
    anchor = resp.pyquery('div.content ul.objects-list a[href="%s"]' % url)
    assert anchor.text() == 'bar (bar-description) - OU2'


def test_add(superuser, app, strong_password):
    # IP restriction feature flag disabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = False

    preferred_color = Attribute.objects.create(
        name='preferred_color',
        label='Preferred color',
        kind='string',
        disabled=False,
        multiple=False,
    )
    phone2 = Attribute.objects.create(
        name='phone2',
        label='Second phone number',
        kind='phone_number',
        disabled=False,
        multiple=False,
    )
    assert APIClient.objects.count() == 0
    role_1 = Role.objects.create(name='role-1', ou=get_default_ou())
    role_2 = Role.objects.create(name='role-2', ou=get_default_ou())
    resp = login(app, superuser, 'a2-manager-api-client-add')
    form = resp.form
    # IP restriction feature flags deleted fields
    assert 'allowed_ip' not in form.fields
    assert 'denied_ip' not in form.fields
    assert 'ip_allow_deny' not in form.fields
    # password is prefilled with a strong password
    proposed_passwd = form.get('apiclient_password').value
    ret, dummy = validate_apiclient_password(proposed_passwd)
    assert ret
    assert ('', False, '---------') in form['ou'].options
    form.set('name', 'api-client-name')
    form.set('description', 'api-client-description')
    form.set('identifier', 'api-client-identifier')
    form.set('apiclient_password', strong_password)
    form['apiclient_roles'].force_value([role_1.id, role_2.id])
    form.set('allowed_user_attributes', [preferred_color.id, phone2.id])
    response = form.submit().follow()
    assert APIClient.objects.count() == 1
    api_client = APIClient.objects.get(name='api-client-name')
    assert set(api_client.apiclient_roles.all()) == {role_1, role_2}
    assert set(api_client.allowed_user_attributes.all()) == {preferred_color, phone2}
    assert urlparse(response.request.url).path == api_client.get_absolute_url()
    assert api_client.allowed_ip == ''
    assert api_client.denied_ip == ''


@pytest.mark.parametrize(
    'weak_password,hint',
    (
        ['toto', '43 characters, 1 uppercase letter, 1 digit'],
        ['Toto', '43 characters, 1 digit'],
        ['12345678901234567890123456789012345678901234567890', '1 uppercase letter, 1 lowercase letter'],
    ),
)
def test_weak_password(superuser, app, weak_password, hint):
    resp = login(app, superuser, 'a2-manager-api-client-add')
    form = resp.form
    form.set('name', 'api-client-name')
    form.set('description', 'api-client-description')
    form.set('identifier', 'api-client-identifier')
    form.set('apiclient_password', weak_password)
    resp = form.submit()

    assert 'There were errors processing your form.' in resp
    assert 'Password must contain at least %s.' % hint in resp

    # Test password check on edition
    api_client = APIClient.objects.create_user(
        name='foo', description='foo-api', identifier='foo-id', password='hackmeplz', ou=get_default_ou()
    )
    api_client = APIClient.objects.get(pk=api_client.pk)
    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form

    assert (
        resp.pyquery('div#help_text_id_apiclient_password.hint p')[0].text.strip()
        == 'The password will remain unchanged if this field is left empty.'
    )
    assert resp.pyquery('div#help_text_id_apiclient_password.hint p a').text() == 'Generate new password'

    assert form.fields['apiclient_password'][0].value == ''
    form.fields['apiclient_password'][0].value = weak_password
    resp = form.submit()
    assert 'There were errors processing your form.' in resp
    assert 'Password must contain at least %s.' % hint in resp


def test_add_ip_restricted(superuser, app, strong_password):
    # IP restriction feature flag enabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = True

    preferred_color = Attribute.objects.create(
        name='preferred_color',
        label='Preferred color',
        kind='string',
        disabled=False,
        multiple=False,
    )
    phone2 = Attribute.objects.create(
        name='phone2',
        label='Second phone number',
        kind='phone_number',
        disabled=False,
        multiple=False,
    )
    assert APIClient.objects.count() == 0
    role_1 = Role.objects.create(name='role-1', ou=get_default_ou())
    role_2 = Role.objects.create(name='role-2', ou=get_default_ou())
    resp = login(app, superuser, 'a2-manager-api-client-add')
    form = resp.form
    # password is prefilled
    proposed_password = form.get('apiclient_password').value
    assert len(proposed_password) >= 43
    assert ('', False, '---------') in form['ou'].options
    form.set('name', 'api-client-name')
    form.set('description', 'api-client-description')
    form.set('identifier', 'api-client-identifier')
    form.set('apiclient_password', strong_password)
    form.set('allowed_ip', '127.0.0.0/16     ::1\r\n')
    form.set('denied_ip', ' # forbid ipv6\r\n::/0   \r\n\r\n# lan\r\n192.168.0.1/32')
    form.set('ip_allow_deny', 0)
    form['apiclient_roles'].force_value([role_1.id, role_2.id])
    form.set('allowed_user_attributes', [preferred_color.id, phone2.id])
    response = form.submit().follow()
    assert APIClient.objects.count() == 1
    api_client = APIClient.objects.get(name='api-client-name')
    assert api_client.password != strong_password
    assert api_client.check_password(strong_password)
    assert set(api_client.apiclient_roles.all()) == {role_1, role_2}
    assert set(api_client.allowed_user_attributes.all()) == {preferred_color, phone2}
    assert urlparse(response.request.url).path == api_client.get_absolute_url()
    assert api_client.allowed_ip == '127.0.0.0/16\n::1'
    assert api_client.denied_ip == '# forbid ipv6\n::/0\n\n# lan\n192.168.0.1'
    assert api_client.ip_allow_deny is False

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form
    assert form.fields['allowed_ip'][0].value == '127.0.0.0/16\n::1'
    assert form.fields['denied_ip'][0].value == '# forbid ipv6\n::/0\n\n# lan\n192.168.0.1'


def test_add_local_admin(admin_ou1, app, ou1, ou2):
    assert APIClient.objects.count() == 0
    resp = login(app, admin_ou1, 'a2-manager-api-client-add')
    form = resp.form
    assert len(form['ou'].options) == 1
    assert ('', False, '---------') not in form['ou'].options
    assert form['ou'].options[0][2] == 'OU1'

    role = Role.objects.get(slug='_a2-manager-of-api-clients-%s' % ou2.slug)
    admin_ou1.roles.add(role)
    resp = app.get(reverse('a2-manager-api-client-add'))
    assert len(resp.form['ou'].options) == 2
    assert ('', False, '---------') not in form['ou'].options


def test_add_description_non_mandatory(superuser, app, strong_password):
    assert APIClient.objects.count() == 0
    role_1 = Role.objects.create(name='role-1', ou=get_default_ou())
    role_2 = Role.objects.create(name='role-2', ou=get_default_ou())
    resp = login(app, superuser, 'a2-manager-api-client-add')
    form = resp.form
    form.set('name', 'api-client-name')
    form.set('identifier', 'api-client-identifier')
    form.set('apiclient_password', strong_password)
    form['apiclient_roles'].force_value([role_1.id, role_2.id])
    response = form.submit().follow()
    assert APIClient.objects.count() == 1
    api_client = APIClient.objects.get(name='api-client-name')
    assert api_client.check_password(strong_password)
    assert api_client.password != strong_password
    assert set(api_client.apiclient_roles.all()) == {role_1, role_2}
    assert urlparse(response.request.url).path == api_client.get_absolute_url()


def test_detail(superuser, app, phone_activated_authn):
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = False

    role_1 = Role.objects.create(name='role-1')
    role_2 = Role.objects.create(name='role-2')
    api_client = APIClient.objects.create_user(
        name='foo',
        description='foo-description',
        identifier='foo-identifier',
        password='foo-password',
        restrict_to_anonymised_data=True,
    )
    api_client.apiclient_roles.add(role_1, role_2)
    api_client.allowed_user_attributes.add(phone_activated_authn.phone_identifier_field)
    resp = login(app, superuser, api_client.get_absolute_url())
    assert 'foo-description' in resp.text
    assert 'Identifier: foo-identifier' in resp.text
    assert 'foo-description' in resp.text
    assert 'Restricted to anonymised data' in resp.text
    assert 'role-1' in resp.text
    assert 'role-2' in resp.text
    assert 'Allowed user attributes' in resp.text
    assert 'phone' in resp.text

    assert 'IP restrictions' not in resp.text

    edit_button = resp.pyquery(
        'span.actions a[href="%s"]' % reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk})
    )
    assert edit_button
    assert edit_button.text() == 'Edit'
    delete_button = resp.pyquery(
        'span.actions a[href="%s"]' % reverse('a2-manager-api-client-delete', kwargs={'pk': api_client.pk})
    )
    assert delete_button
    assert delete_button.text() == 'Delete'

    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = True
    resp = app.get(api_client.get_absolute_url())
    assert '<li>IP restrictions' in resp.text
    assert '(evaluation order Deny/Allow)' in resp.text
    assert '<li>Deny: None</li>' in resp.text
    assert '<li>Allow: None</li>' in resp.text

    api_client.allowed_ip = '127.0.0.1\n127.0.0.2'
    api_client.save()
    resp = app.get(api_client.get_absolute_url())

    assert '<li>IP restrictions' in resp.text
    assert '(evaluation order Deny/Allow)' in resp.text
    assert '<li>Allow: 127.0.0.1, 127.0.0.2</li>' in resp.text
    assert '<li>Deny: None</li>' in resp.text

    api_client.denied_ip = '1.2.3.4\n5.6.7.8'
    api_client.ip_allow_deny = True
    api_client.save()
    resp = app.get(api_client.get_absolute_url())

    assert '<li>IP restrictions' in resp.text
    assert '(evaluation order Allow/Deny)' in resp.text
    assert '<li>Allow: 127.0.0.1, 127.0.0.2</li>' in resp.text
    assert '<li>Deny: 1.2.3.4, 5.6.7.8</li>' in resp.text


def test_manager_apiclient_roles_list(app, superuser, ou1, ou2):
    login(app, superuser, '/')

    default_ou = get_default_ou()
    parent_role = Role.objects.create(name='parent', slug='parent', ou=default_ou)
    child_role = Role.objects.create(name='child', slug='child', ou=default_ou)
    child_role.add_parent(parent_role)
    indirect_parent = Role.objects.create(name='Gparent', slug='parent2', ou=default_ou)
    parent_role.add_parent(indirect_parent)
    role2 = Role.objects.create(name='Gparent2', slug='role2', ou=default_ou)
    parent_role.add_parent(role2)
    other_role = Role.objects.create(name='other', slug='other', ou=ou2)

    api_client = APIClient.objects.create_user(name='foo', identifier='foo-id', password='foo')
    api_client.apiclient_roles.set([child_role.pk, role2])

    resp = app.get(reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}))

    assert (
        'Restricted to data within organizational unit '
        f'<a href="/manage/organizational-units/{default_ou.pk}/">{default_ou.name}</a>' in resp
    )
    # Same OU for all roles (parent & child) no OU displayed
    for role in (child_role, parent_role, indirect_parent, role2):
        link = resp.pyquery('ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': role.pk}))
        assert link
        assert len(link) == 1
        if role in (child_role, role2):
            assert link.parent().parent().parent().text().startswith('Roles:\n')
        else:
            assert link.parent().parent().parent().text().startswith('Inherited roles:\n')
        assert link.text() == role.name
        assert not link.next()

    assert not resp.pyquery(
        'ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': other_role.pk})
    )

    # mutlitple OU or api_client OU differ from role's OU, displaying them
    for other_ou_role in (child_role, parent_role, indirect_parent, role2):
        other_ou_role.ou = ou1
        other_ou_role.save()
        resp = app.get(reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}))

        link = resp.pyquery('ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': role.pk}))
        assert link
        assert len(link) == 1
        if role in (child_role, role2):
            assert link.parent().parent().parent().text().startswith('Roles:\n')
        else:
            assert link.parent().parent().parent().text().startswith('Inherited roles:\n')
        assert link.text() == role.name
        assert link.next().text() == role.ou.name
        assert link.next().attr['href'] == reverse('a2-manager-ou-detail', kwargs={'pk': role.ou.pk})
        assert link.parent().text() == '%s (%s)' % (role.name, role.ou.name)

    # Checking for role without OU
    for empty_ou_role in (child_role, parent_role, indirect_parent, role2):
        empty_ou_role.ou = None
        empty_ou_role.save()

    api_client.apiclient_roles.set([child_role.pk])

    resp = app.get(reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}))
    for role in (child_role, parent_role, indirect_parent, role2):
        link = resp.pyquery('ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': role.pk}))
        assert link
        assert len(link) == 1
        if role == child_role:
            assert link.parent().parent().parent().text().startswith('Roles:\n')
        else:
            assert link.parent().parent().parent().text().startswith('Inherited roles:\n')
        assert link.text() == role.name
        assert not link.next()
        assert link.parent().text() == '%s (No organizational unit)' % role.name

    # Check for unlimited OU-wise access explanation message
    api_client.ou = None
    api_client.save()
    resp = app.get(reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}))
    assert 'Unrestricted data access regardless of its organizational unit.' in resp


def test_manager_apiclient_roles_list_perm(app, admin_ou1, ou1, ou2):
    api_client_admin_role = Role.objects.get(slug='_a2-manager-of-api-clients')
    admin_ou1.roles.add(api_client_admin_role)
    admin_ou1.save()

    login(app, admin_ou1, '/')

    child_role = Role.objects.create(name='child', slug='child', ou=get_default_ou())
    parent_role = Role.objects.create(name='parent', slug='parent', ou=ou1)
    indirect_parent = Role.objects.create(name='Gparent', slug='parent2', ou=ou2)
    child_role.add_parent(parent_role)
    parent_role.add_parent(indirect_parent)

    api_client = APIClient.objects.create_user(name='foo', identifier='foo-id', password='foo')
    api_client.apiclient_roles.set([child_role.pk])

    resp = app.get(reverse('a2-manager-api-client-detail', kwargs={'pk': api_client.pk}))

    link = resp.pyquery(
        'ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': parent_role.pk})
    )
    assert link
    assert link.attr['href'] == reverse('a2-manager-role-members', kwargs={'pk': parent_role.pk})
    assert link.text() == parent_role.name
    assert link.next().attr['href'] == reverse('a2-manager-ou-detail', kwargs={'pk': parent_role.ou.pk})
    assert link.next().text() == parent_role.ou.name

    link = resp.pyquery(
        'ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': indirect_parent.pk})
    )
    assert not link
    for li in resp.pyquery('ul li'):
        if li.text and li.text.strip() == '%s (%s)' % (indirect_parent.name, indirect_parent.ou.name):
            assert not list(li.iterchildren())
            assert not list(li.itersiblings())
            break
    else:
        pytest.fail(
            'A <li> without links for "%s (%s)" not found' % (indirect_parent.name, indirect_parent.ou.name)
        )

    link = resp.pyquery(
        'ul li a[href="%s"]' % reverse('a2-manager-role-members', kwargs={'pk': child_role.pk})
    )
    assert not link
    for li in resp.pyquery('ul li'):
        if li.text and li.text.strip() == '%s (%s)' % (child_role.name, child_role.ou.name):
            assert not list(li.iterchildren())
            assert not list(li.itersiblings())
            break
    else:
        pytest.fail('A <li> without links for "%s (%s)" not found' % (child_role.name, child_role.ou.name))


def test_edit(superuser, app, ou1, ou2, strong_password):
    preferred_color = Attribute.objects.create(
        name='preferred_color',
        label='Preferred color',
        kind='string',
        disabled=False,
        multiple=False,
    )
    phone2 = Attribute.objects.create(
        name='phone2',
        label='Second phone number',
        kind='phone_number',
        disabled=False,
        multiple=False,
    )
    role_1 = Role.objects.create(name='role-1', ou=ou1)
    role_2 = Role.objects.create(name='role-2', ou=ou2)
    role_3 = Role.objects.create(name='role-3', ou=ou1)
    api_client = APIClient.objects.create_user(
        name='foo',
        description='foo-description',
        identifier='foo-identifier',
        password='foo-password',
        ou=ou1,
    )
    api_client.allowed_user_attributes.add(preferred_color, phone2)
    api_client.save()
    assert APIClient.objects.count() == 1
    resp = login(app, superuser, 'a2-manager-api-client-edit', kwargs={'pk': api_client.pk})

    password_help_id = resp.form.fields['apiclient_password'][0].attrs['aria-describedby']
    assert (
        resp.pyquery.find(f'#{password_help_id}')[0]
        .text_content()
        .startswith('The password will remain unchanged if this field is left empty.')
    )
    # password is NOT prefilled
    assert resp.form.get('apiclient_password').value == ''
    form = resp.form
    assert set(form.get('allowed_user_attributes').value) == {str(preferred_color.id), str(phone2.id)}
    assert ('', False, '---------') in form['ou'].options
    resp.form.set('apiclient_password', strong_password)
    with pytest.raises(KeyError):
        # forcing values not presented by the Select2ModelMultipleChoiceField,
        # should not happen in UI
        form['apiclient_roles'].force_value([role_1.id, role_2.id])
        form.submit()
    form['apiclient_roles'].force_value([role_1.id, role_3.id])
    form['allowed_user_attributes'].force_value([phone2.id])
    response = form.submit().follow()
    assert urlparse(response.request.url).path == api_client.get_absolute_url()
    assert APIClient.objects.count() == 1
    api_client = APIClient.objects.get(identifier='foo-identifier')
    assert api_client.check_password(strong_password)
    assert api_client.password != strong_password
    assert set(api_client.allowed_user_attributes.all()) == {phone2}

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form
    form.set('ou', ou2.id)
    response = form.submit()
    assert (
        response.pyquery('.error').text()
        == 'The following roles do not belong to organizational unit OU2: role-1, role-3.'
    )
    response.form.set('ou', ou2.id)
    response.form['apiclient_roles'].force_value([])
    response.form['allowed_user_attributes'].force_value([])
    response.form.submit().follow()
    api_client = APIClient.objects.get()
    assert set(api_client.apiclient_roles.all()) == set()
    assert set(api_client.allowed_user_attributes.all()) == set()
    assert api_client.ou == ou2

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form
    form['apiclient_roles'].force_value([role_2.id])
    form['allowed_user_attributes'].force_value([preferred_color.id])
    response = form.submit().follow()
    api_client = APIClient.objects.get()
    assert api_client.ou == ou2
    assert set(api_client.apiclient_roles.all()) == {role_2}
    assert set(api_client.allowed_user_attributes.all()) == {preferred_color}


def test_edit_password(superuser, app, ou1, strong_password):
    api_client = APIClient.objects.create_user(
        name='foo', description='foo-api', identifier='foo-id', password='hackmeplz', ou=ou1
    )
    api_client = APIClient.objects.get(pk=api_client.pk)
    assert api_client.check_password('hackmeplz')

    resp = login(app, superuser, 'a2-manager-api-client-edit', kwargs={'pk': api_client.pk})
    form = resp.form
    # password not prefilled on edit
    assert form.fields['apiclient_password'][0].value == ''
    form.submit().follow()

    api_client = APIClient.objects.get(pk=api_client.pk)
    assert api_client.check_password('hackmeplz')

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form
    assert form.fields['apiclient_password'][0].value == ''
    form.fields['apiclient_password'][0].value = 'toto'
    resp = form.submit()

    assert 'There were errors processing your form.' in resp
    assert 'Password must contain at least 43 characters, 1 uppercase letter, 1 digit.' in resp
    api_client = APIClient.objects.get(pk=api_client.pk)
    assert api_client.check_password('hackmeplz')  # password untouched

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client.pk}))
    form = resp.form
    form.fields['apiclient_password'][0].value = strong_password
    resp = form.submit().follow()

    api_client = APIClient.objects.get(pk=api_client.pk)
    assert api_client.check_password(strong_password)


def test_edit_local_admin(admin_ou1, app, ou1, ou2, strong_password):
    role_1 = Role.objects.create(name='role-1', ou=ou1)
    role_2 = Role.objects.create(name='role-2', ou=ou2)
    role_3 = Role.objects.create(name='role-3', ou=ou1)
    api_client_ou1 = APIClient.objects.create_user(
        name='foo',
        description='foo-description',
        identifier='foo-description',
        password='foo-password',
        ou=ou1,
    )
    api_client_ou2 = APIClient.objects.create_user(
        name='bar',
        description='bar-description',
        identifier='bar-description',
        password='bar-password',
        ou=ou2,
    )
    resp = login(app, admin_ou1, 'a2-manager-api-client-edit', kwargs={'pk': api_client_ou1.pk})
    form = resp.form
    assert form.get('apiclient_password').value == ''
    resp.form.set('apiclient_password', strong_password)
    assert ('', False, '---------') not in form['ou'].options
    with pytest.raises(KeyError):
        # forcing values not presented by the Select2ModelMultipleChoiceField,
        # should not happen in UI
        form['apiclient_roles'].force_value([role_1.id, role_2.id])
        form.submit()
    form['apiclient_roles'].force_value([role_1.id, role_3.id])
    response = form.submit().follow()
    assert urlparse(response.request.url).path == api_client_ou1.get_absolute_url()
    api_client = APIClient.objects.get(identifier='foo-description')
    assert api_client.check_password(strong_password)

    role = Role.objects.get(slug='_a2-manager-of-api-clients-%s' % ou2.slug)
    admin_ou1.roles.add(role)
    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': api_client_ou2.pk}))
    assert ('', False, '---------') not in form['ou'].options
    resp.form.set('ou', ou1.id)
    resp.form.submit().follow()
    assert APIClient.objects.filter(ou=ou1).count() == 2


def test_edit_duplicated_identifier(app, admin):
    apiclient0 = APIClient.objects.create_user(name='original', identifier='uniq', password='foo')
    apiclient1 = APIClient.objects.create_user(name='foo', identifier='foo', password='foo')
    apiclient2 = APIClient.objects.create_user(
        name='foo', identifier='foo2', identifier_legacy='foo', password='foo'
    )
    assert apiclient0.identifier_legacy is None
    assert apiclient1.identifier_legacy is None
    resp = login(app, admin, 'a2-manager-api-client-edit', kwargs={'pk': apiclient0.pk})
    assert len(resp.pyquery.find('div.hint#help_text_id_identifier')) == 0
    assert len(resp.pyquery.find('div.error#error_id_identifier')) == 0
    resp.form.set('identifier', 'foo')
    resp = resp.form.submit()
    assert 'Identifier is not unique, please change' in resp.pyquery.find('.errornotice').text()

    for pk in (apiclient1.pk, apiclient2.pk):
        resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': pk}))
        assert (
            resp.pyquery.find('div.hint#help_text_id_identifier').text()
            == 'Duplicated identifier, please change it'
        )
        assert resp.form.fields['identifier'][0].value == 'foo'
        resp.form.set('identifier', 'uniq')
        resp = resp.form.submit()
        assert 'Identifier is not unique, please change' in resp.pyquery.find('.errornotice').text()

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': apiclient1.pk}))
    resp.form.set('identifier', 'foo2')
    resp = resp.form.submit()
    assert 'Identifier is not unique, please change' in resp.pyquery.find('.errornotice').text()
    resp.form.set('identifier', 'foo3')
    resp = resp.form.submit().follow()
    apiclient1.refresh_from_db()
    apiclient2.refresh_from_db()
    assert apiclient1.identifier_legacy is None
    assert apiclient2.identifier_legacy is None

    apiclient0.identifier = 'toto'
    apiclient1.identifier_legacy = 'toto'
    apiclient2.identifier_legacy = 'toto'
    apiclient0.save()
    apiclient1.save()
    apiclient2.save()

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': apiclient1.pk}))
    resp.form.set('identifier', 'toto2')
    resp = resp.form.submit().follow()
    apiclient0.refresh_from_db()
    apiclient1.refresh_from_db()
    apiclient2.refresh_from_db()
    assert apiclient1.identifier_legacy is None
    assert apiclient0.identifier == 'toto'
    assert apiclient2.identifier_legacy == 'toto'

    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': apiclient0.pk}))
    assert resp.form.fields['identifier'][0].value == 'toto'
    resp = app.get(reverse('a2-manager-api-client-edit', kwargs={'pk': apiclient2.pk}))
    assert resp.form.fields['identifier'][0].value == 'toto'
    resp.form.set('identifier', 'toto3')
    resp.form.submit().follow()
    apiclient0.refresh_from_db()
    apiclient2.refresh_from_db()
    assert apiclient0.identifier_legacy is None
    assert apiclient2.identifier_legacy is None


def test_delete(superuser, app):
    api_client = APIClient.objects.create_user(
        name='foo', description='foo-description', identifier='foo-identifier', password='foo-password'
    )
    assert APIClient.objects.count() == 1
    resp = login(app, superuser, 'a2-manager-api-client-delete', kwargs={'pk': api_client.pk})
    response = resp.form.submit().follow()
    assert urlparse(response.request.url).path == reverse('a2-manager-api-clients')
    assert APIClient.objects.count() == 0
