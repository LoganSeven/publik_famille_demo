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
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from webtest import Upload

from authentic2.a2_rbac.models import MANAGE_MEMBERS_OP, VIEW_OP
from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role
from authentic2.a2_rbac.utils import get_default_ou, get_operation
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal.models import Event
from authentic2.models import Service, Setting
from authentic2.validators import EmailValidator

from .utils import assert_event, get_link_from_mail, login, logout, request_select2, text_content

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_login_hint_backoffice(app):
    app.get('/manage/')
    assert app.session['login-hint'] == ['backoffice']


def test_manager_login_superuser(superuser, app):
    response = login(app, superuser, '/manage/')

    # all main sections are visible
    sections = ['users', 'roles', 'ous', 'services']
    for section in sections:
        path = reverse('a2-manager-%s' % section)
        assert response.pyquery.remove_namespaces()('a.button[href=\'%s\']' % path)

    # sidbebar link are visible but not ldap
    conf_entries = ['authn', 'journal', 'api-clients']
    assert len(response.pyquery('div.a2-manager-id-tools_content').children()) == 3
    for entry in conf_entries:
        assert response.pyquery(f'a#{entry}')
    assert not response.pyquery('a#tech-info')

    # mock ldap configuration, then ldap link is visible
    mocked_config = [{'ldap_uri': 'ldaps://soundsfake.nowhere.null'}]
    with mock.patch('authentic2.backends.ldap_backend.LDAPBackend.get_config', return_value=mocked_config):
        response = app.get('/manage/')
    assert response.pyquery('a#tech-info')


def test_manager_login_admin(admin, app):
    login(app, admin)
    # mock ldap configuration during manager homepage view
    mocked_config = [{'ldap_uri': 'ldaps://soundsfake.nowhere.null'}]
    with mock.patch('authentic2.backends.ldap_backend.LDAPBackend.get_config', return_value=mocked_config):
        response = app.get('/manage/')

    # all main sections are visible
    sections = ['users', 'roles', 'ous', 'services']
    for section in sections:
        path = reverse('a2-manager-%s' % section)
        assert response.pyquery.remove_namespaces()('a.button[href=\'%s\']' % path)
    # only journal, api clients and authenticators are visible in the sidebar
    assert len(response.pyquery('div.a2-manager-id-tools_content').children()) == 3
    assert response.pyquery('a#journal')
    assert response.pyquery('a#api-clients')
    assert response.pyquery('a#authn')


def test_manager_create_ou(superuser_or_admin, app):
    ou_add = login(app, superuser_or_admin, path=reverse('a2-manager-ou-add'))
    form = ou_add.form
    assert 'name' in form.fields
    excluded_fields = (
        'slug',
        'default',
        'username_is_unique',
        'email_is_unique',
        'validate_emails',
        'show_username',
        'check_required_on_login_attributes',
        'user_can_reset_password',
        'user_add_password_policy',
        'clean_unused_accounts_alert',
        'clean_unused_accounts_deletion',
        'home_url',
        'logo',
        'colour',
    )
    for field in excluded_fields:
        assert field not in form.fields

    form.set('name', 'New OU')
    response = form.submit().follow()
    assert 'New OU' in response
    assert OU.objects.count() == 2
    assert OU.objects.get(name='New OU').slug == 'new-ou'

    # Test slug collision
    OU.objects.filter(name='New OU').update(name='Old OU')
    response = form.submit().follow()
    assert 'Old OU' in response
    assert 'New OU' in response
    assert OU.objects.get(name='Old OU').slug == 'new-ou'
    assert OU.objects.get(name='New OU').slug == 'new-ou-1'
    assert OU.objects.count() == 3


def test_manager_create_role(superuser_or_admin, app):
    non_admin_roles = Role.objects.exclude(slug__startswith='_')

    ou_add = login(app, superuser_or_admin, reverse('a2-manager-role-add'))
    form = ou_add.form
    assert 'name' in form.fields
    assert 'description' in form.fields
    assert 'ou' not in form.fields
    assert 'slug' not in form.fields
    form.set('name', 'New role')
    response = form.submit().follow()
    assert non_admin_roles.count() == 1
    role = non_admin_roles.get()
    assert response.request.path == reverse('a2-manager-role-members', kwargs={'pk': role.pk})
    assert role.uuid in response.text
    role_list = app.get(reverse('a2-manager-roles'))
    assert 'New role' in role_list

    # Test slug collision
    non_admin_roles.update(name='Old role')
    response = form.submit().follow()
    role_list = app.get(reverse('a2-manager-roles'))
    assert 'New role' in role_list
    assert 'Old role' in role_list
    assert non_admin_roles.count() == 2
    assert non_admin_roles.get(name='New role').slug == 'new-role-1'
    assert non_admin_roles.get(name='Old role').slug == 'new-role'

    assert non_admin_roles.filter(name='New role').update(name='New role 0')
    role3_add = app.get(reverse('a2-manager-role-add'))
    form = role3_add.form
    form.set('name', 'New role')
    form.submit().follow()
    assert non_admin_roles.count() == 3
    assert non_admin_roles.get(name='New role').slug == 'new-role-2'

    # Test multi-ou form
    new_ou = OU.objects.create(name='New OU', slug='new-ou')
    ou_add = app.get(reverse('a2-manager-role-add'))
    form = ou_add.form
    assert 'name' in form.fields
    assert 'description' in form.fields
    assert 'ou' in form.fields
    options = [o[2] for o in form.fields['ou'][0].options]
    assert len(options) == 3
    assert '---------' in options
    assert 'New OU' in options

    # Test slug generation on different OU
    form.set('ou', new_ou.id)
    form.set('name', 'New role')
    form.submit().follow()
    assert non_admin_roles.get(name='New role', ou=new_ou).slug == 'new-role'


def test_manager_create_role_long_name(superuser, app):
    resp = login(app, superuser, reverse('a2-manager-role-add'))
    resp.form['name'] = 'a' * 300
    resp = resp.form.submit().follow()

    role = Role.objects.exclude(slug__startswith='_').get()
    assert role.name == 'a' * 300
    assert role.slug == 'a' * 252 + '4e54'
    assert role.get_admin_role().slug == '_a2-managers-of-role-' + 'a' * 231 + '7a7e'


def test_manager_edit_role(superuser_or_admin, app, simple_role):
    resp = login(app, superuser_or_admin, '/manage/roles/%s/edit/' % simple_role.pk)
    resp.form['details'] = 'xxx'
    resp.form['emails_to_members'] = False
    resp = resp.form.submit().follow()

    simple_role.refresh_from_db()
    assert simple_role.details == 'xxx'
    assert simple_role.emails == []
    assert simple_role.emails_to_members is False

    resp = app.get('/manage/roles/%s/edit/' % simple_role.pk)
    resp.form['emails'] = 'test@example.com'
    resp = resp.form.submit().follow()

    simple_role.refresh_from_db()
    assert simple_role.emails == ['test@example.com']

    resp = app.get('/manage/roles/%s/edit/' % simple_role.pk)
    resp.form['emails'] = 'test@example.com, hop@example.com'
    resp = resp.form.submit().follow()

    simple_role.refresh_from_db()
    assert set(simple_role.emails) == {'test@example.com', 'hop@example.com'}

    resp = app.get('/manage/roles/%s/edit/' % simple_role.pk)
    resp.form['emails'] = 'xxx'
    resp = resp.form.submit()
    assert 'Item 0 is invalid: Enter a valid email address.' in resp.text


def test_manager_edit_role_slug(superuser_or_admin, app, simple_role):
    assert Role.objects.get(name='simple role').slug == 'simple-role'
    resp = login(app, superuser_or_admin, reverse('a2-manager-role-edit', kwargs={'pk': simple_role.pk}))
    form = resp.form
    assert 'slug' in form.fields
    form.set('slug', 'new-simple-role-slug')
    form.submit().follow()
    assert Role.objects.get(name='simple role').slug == 'new-simple-role-slug'


def test_manager_user_password_reset(app, superuser, simple_user):
    resp = login(app, superuser, reverse('a2-manager-user-detail', kwargs={'pk': simple_user.pk}))
    assert len(mail.outbox) == 0
    resp = resp.forms['object-actions'].submit('password_reset')
    assert 'A mail was sent to' in resp
    assert len(mail.outbox) == 1
    assert_event('manager.user.password.reset.request', user=superuser, session=app.session)
    assert not Event.objects.filter(type__name='user.password.reset.request').exists()

    url = get_link_from_mail(mail.outbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get('/logout/').maybe_follow()
    resp = app.get(relative_url, status=200)
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp = resp.form.submit().follow()
    assert str(app.session['_auth_user_id']) == str(simple_user.pk)


def test_manager_user_change_password_form(app, simple_user):
    from authentic2.manager.forms import UserChangePasswordForm

    data = {
        'password1': 'Password0',
        'password2': 'Password0',
    }

    form = UserChangePasswordForm(instance=simple_user, data=data)
    assert form.fields['password1'].widget.min_strength is None
    assert 'password1' not in form.errors

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    form = UserChangePasswordForm(instance=simple_user, data=data)
    assert form.fields['password1'].widget.min_strength == 3
    assert form.errors['password1'] == ['This password is not strong enough.']


def test_manager_user_detail_by_uuid(app, superuser, simple_user, simple_role):
    simple_user.roles.add(simple_role)
    url = reverse('a2-manager-user-by-uuid-detail', kwargs={'slug': simple_user.uuid})
    resp = login(app, superuser, url)
    assert '<h3>Actions</h3>' in resp.text
    assert simple_user.first_name.encode('utf-8') in resp.content
    assert 'simple role' in resp.html.find('div', {'class': 'user-roles'}).ul.li.text

    # if user has roles on multiple, roles are grouped by OU
    simple_user.roles.add(Role.objects.create(name='global role', slug='global-role', ou=None))
    resp = app.get(url)
    html_roles = resp.html.find('div', {'class': 'user-roles'})
    assert 'Default organizational unit' in html_roles.ul.find_all('li', recursive=False)[0].next
    assert 'simple role' in html_roles.ul.find_all('li', recursive=False)[0].ul.li.text
    assert 'All organizational units' in html_roles.ul.find_all('li', recursive=False)[1].next
    assert 'global role' in html_roles.ul.find_all('li', recursive=False)[1].ul.li.text


def test_manager_user_edit_by_uuid(app, superuser, simple_user):
    url = reverse('a2-manager-user-by-uuid-edit', kwargs={'slug': simple_user.uuid})
    resp = login(app, superuser, url)
    assert '<h3>Actions</h3>' not in resp.text
    assert simple_user.first_name.encode('utf-8') in resp.content


def test_manager_stress_create_user(superuser_or_admin, app, mailoutbox):
    new_ou = OU.objects.create(name='new ou', slug='new-ou')
    url = reverse('a2-manager-user-add', kwargs={'ou_pk': new_ou.pk})
    # create first user with john.doe@gmail.com ou OU1 : OK

    assert len(mailoutbox) == 0
    assert User.objects.filter(ou_id=new_ou.id).count() == 0
    for _ in range(5):
        ou_add = login(app, superuser_or_admin, url)
        form = ou_add.form
        form.set('first_name', 'John')
        form.set('last_name', 'Doe')
        form.set('email', 'john.doe@gmail.com')
        form.set('password1', 'ABcd1234')
        form.set('password2', 'ABcd1234')
        form.set('send_mail', True)
        form.submit().follow()
        app.get('/logout/').form.submit()
    assert User.objects.filter(ou_id=new_ou.id).count() == 5
    assert len(mailoutbox) == 5


def test_role_members_from_ou(app, superuser, simple_user, settings):
    assert superuser.ou is None and simple_user.ou == get_default_ou()
    r = Role.objects.create(name='role', slug='role', ou=get_default_ou())
    url = reverse('a2-manager-role-members', kwargs={'pk': r.pk})

    response = login(app, superuser, url)
    select2_json = request_select2(app, response, fetch_all=True)
    assert len([x for x in select2_json['results'] if x['id'].startswith('user')]) == 2

    settings.A2_MANAGER_ROLE_MEMBERS_FROM_OU = True
    response = app.get(url)
    select2_json = request_select2(app, response, fetch_all=True)
    user_choices = [x for x in select2_json['results'] if x['id'].startswith('user')]
    assert len(user_choices) == 1
    assert user_choices[0]['id'] == 'user-%s' % simple_user.pk


def test_manager_create_user(superuser_or_admin, app, settings):
    ou1 = OU.objects.create(name='OU1', slug='ou1')
    ou2 = OU.objects.create(name='OU2', slug='ou2', email_is_unique=True)

    assert User.objects.filter(ou=ou1).count() == 0
    assert User.objects.filter(ou=ou2).count() == 0

    # create first user with john.doe@gmail.com ou OU1 : OK
    url = reverse('a2-manager-user-add', kwargs={'ou_pk': ou1.pk})
    ou_add = login(app, superuser_or_admin, url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit().follow()
    assert User.objects.filter(ou=ou1).count() == 1

    # create second user with john.doe@gmail.com ou OU1 : OK
    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit().follow()
    assert User.objects.filter(ou=ou1).count() == 2

    # create first user with john.doe@gmail.com ou OU2 : OK
    url = reverse('a2-manager-user-add', kwargs={'ou_pk': ou2.pk})
    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit().follow()
    assert User.objects.filter(ou=ou2).count() == 1

    # create second user with john.doe@gmail.com ou OU2 : NOK
    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit()
    assert User.objects.filter(ou=ou2).count() == 1
    assert 'This email address is already in use.' in response

    # first user with john.doe@gmail.com/ou2 marked as deleted
    john = User.objects.get(email='john.doe@gmail.com', ou=ou2)
    john.delete()

    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit()
    assert User.objects.filter(ou=ou2).count() == 1

    # create first user with john.doe2@gmail.com ou OU2 : OK
    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'Jane')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe2@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit().follow()

    # try to change user email from john.doe2@gmail.com to
    # john.doe@gmail.com in OU2 : NOK
    response.forms['id_user_edit_form'].set('email', 'john.doe@gmail.com')
    response = form.submit()
    assert 'This email address is already in use.' in response

    # create first user with email john.doe@gmail.com in OU1: NOK
    settings.A2_EMAIL_IS_UNIQUE = True
    url = reverse('a2-manager-user-add', kwargs={'ou_pk': ou1.pk})
    User.objects.filter(ou=ou1).delete()
    assert User.objects.filter(ou=ou1).count() == 0
    ou_add = app.get(url)
    form = ou_add.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit()
    assert User.objects.filter(ou=ou1).count() == 0
    assert 'This email address is already in use.' in response

    form = response.form
    form.set('email', 'john.doe3@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit().follow()
    assert User.objects.filter(ou=ou1).count() == 1

    # try to change user email from john.doe3@gmail.com to
    # john.doe@gmail.com in OU2 : NOK
    response.forms['id_user_edit_form'].set('email', 'john.doe@gmail.com')
    response = form.submit()
    assert 'This email address is already in use.' in response

    # check redirect to default ou
    url1 = reverse('a2-manager-user-add-default-ou')
    url2 = reverse('a2-manager-user-add', kwargs={'ou_pk': get_default_ou().pk})
    resp = app.get(url1)
    assert urlparse(resp['Location']).path == url2


def test_manager_create_user_email_validation(superuser_or_admin, app, settings, monkeypatch):
    settings.A2_VALIDATE_EMAIL_DOMAIN = True
    monkeypatch.setattr(EmailValidator, 'query_mxs', lambda x, y: [])
    ou1 = OU.objects.create(name='OU1', slug='ou1')

    url = reverse('a2-manager-user-add', kwargs={'ou_pk': ou1.pk})
    resp = login(app, superuser_or_admin, url)
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp.form.set('email', 'john.doe@entrouvert.com')
    resp.form.set('password1', 'ABcd1234')
    resp.form.set('password2', 'ABcd1234')
    resp = resp.form.submit()
    assert 'Email domain (entrouvert.com) does not exists' in resp.text

    monkeypatch.setattr(EmailValidator, 'query_mxs', lambda x, y: ['mx1.entrouvert.org'])
    resp.form.submit()
    assert User.objects.filter(email='john.doe@entrouvert.com').count() == 1


def test_app_setting_login_url(app, settings):
    settings.A2_MANAGER_LOGIN_URL = '/other_login/'
    response = app.get('/manage/')
    assert urlparse(response['Location']).path == settings.A2_MANAGER_LOGIN_URL
    assert urlparse(response['Location']).query == 'next=/manage/'


def test_manager_one_ou(app, superuser, admin, simple_role, settings):
    def test_user_listing(user):
        response = login(app, user, '/manage/')

        # test user listing ou search
        response = response.click(href='users')
        assert 'search-ou' not in response.form.fields
        assert len(response.form.fields['search-text']) == 1
        # verify table shown
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 2
        assert {e.text for e in q('table tbody td.username')} == {'admin', 'superuser'}

        # test user's role page
        response = app.get('/manage/users/%d/roles/' % admin.pk)
        form = response.forms['search-form']
        assert 'search-ou' not in form.fields
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == 'simple role'

        form.set('search-internals', True)
        response = form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 8
        # admin enroled only in the Manager role, other roles are inherited
        assert len(q('table tbody tr td.via')) == 8
        assert len(q('table tbody tr td.via:empty')) == 2
        for elt in q('table tbody td.name a'):
            assert 'Manager' in elt.text or elt.text == 'simple role'

        form.set('search-limit_to_user', True)
        response = form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == 'Manager'

        # test role listing
        response = app.get('/manage/roles/')
        assert [x.text for x in response.pyquery('td.slug')] == ['simple-role']
        assert 'search-ou' not in response.form.fields
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        assert q('table tbody td.name').text() == 'simple role'

        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 8
        for elt in q('table tbody td.name a'):
            assert 'Manager' in elt.text or elt.text == 'simple role'

    test_user_listing(admin)
    app.session.flush()
    test_user_listing(superuser)


def test_manager_many_ou(app, superuser, admin, simple_role, role_ou1, admin_ou1, settings, ou1):
    def test_user_listing_admin(user):
        response = login(app, user, '/manage/')

        # test user listing ou search
        response = response.click(href='users')
        assert len(response.form.fields['search-ou']) == 1
        assert len(response.form.fields['search-text']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 4
        for key, checked, _ in options:
            assert not checked or key == 'all'
        assert 'all' in [o[0] for o in options]
        assert 'none' in [o[0] for o in options]
        # verify table shown
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 3
        assert {e.text for e in q('table tbody td.username')} == {'admin', 'superuser', 'admin.ou1'}

        # test user's role page
        response = app.get('/manage/users/%d/roles/' % admin.pk)
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 4
        for key, checked, dummy in options:
            assert not checked or key == str(get_default_ou().pk)
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == 'simple role'

        response.form.set('search-ou', 'all')
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == 'None'

        form = response.forms['search-form']
        form.set('search-internals', True)
        response = form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 7
        # admin enroled only in the Manager role, other roles are inherited
        assert len(q('table tbody tr td.via')) == 7
        assert len(q('table tbody tr td.via:empty')) == 1
        for elt in q('table tbody td.name a'):
            assert 'Manager' in elt.text

        form = response.forms['search-form']
        form.set('search-ou', 'none')
        form.set('search-internals', True)
        response = form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 9
        for elt in q('table tbody td.name a'):
            assert 'Manager' in elt.text

        with override_settings(A2_MANAGER_ROLE_MEMBERS_FROM_OU=True):
            form = response.forms['search-form']
            form.set('search-limit_to_user', True)
            response = form.submit()
            q = response.pyquery.remove_namespaces()
            assert len(q('table tbody tr')) == 1
            assert q('table tbody tr').text().startswith('Manager')

        # test role listing
        response = app.get('/manage/roles/')
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 4
        for key, checked, _ in options:
            if key == 'all':
                assert checked
            else:
                assert not checked
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 2
        names = [elt.text for elt in q('table tbody td.name a')]
        assert set(names) == {'simple role', 'role_ou1'}

        response.form.set('search-ou', 'all')
        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 21
        for elt in q('table tbody td.name a'):
            assert (
                'OU1' in elt.text
                or 'Default' in elt.text
                or 'Manager' in elt.text
                or elt.text == 'simple role'
                or elt.text == 'role_ou1'
            )

        response.form.set('search-ou', 'none')
        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 9
        for elt in q('table tbody td.name a'):
            assert 'Manager' in elt.text

    test_user_listing_admin(admin)
    app.session.flush()

    test_user_listing_admin(superuser)
    app.session.flush()

    def test_user_listing_ou_admin(user):
        response = login(app, user, '/manage/')

        # test user listing ou search
        response = response.click(href='users')
        assert len(response.form.fields['search-ou']) == 1
        assert len(response.form.fields['search-text']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 1
        # ou1 is selected
        key, checked, _ = options[0]
        assert checked
        assert key == str(ou1.pk)
        # verify table shown
        q = response.pyquery.remove_namespaces()
        # only admin.ou1 is visible
        assert len(q('table tbody tr')) == 1
        assert {e.text for e in q('table tbody td.username')} == {'admin.ou1'}

        # test user's role page
        response = app.get('/manage/users/%d/roles/' % admin.pk)
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 1
        key, checked, dummy = options[0]
        assert checked
        assert key == str(ou1.pk)
        q = response.pyquery.remove_namespaces()
        # only role_ou1 is visible
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == role_ou1.name

        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 6
        names = {elt.text for elt in q('table tbody td.name a')}
        assert names == {
            'Roles - OU1',
            'Users - OU1',
            'Services - OU1',
            'role_ou1',
            'Authenticators - OU1',
            'API clients - OU1',
        }

        # test role listing
        response = app.get('/manage/roles/')
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 1
        key, checked, _ = options[0]
        assert checked
        assert key == str(ou1.pk)
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        names = [elt.text for elt in q('table tbody td.name a')]
        assert set(names) == {'role_ou1'}

        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 6
        names = {elt.text for elt in q('table tbody td.name a')}
        assert names == {
            'Roles - OU1',
            'Users - OU1',
            'Services - OU1',
            'role_ou1',
            'Authenticators - OU1',
            'API clients - OU1',
        }

    test_user_listing_ou_admin(admin_ou1)


def test_manager_many_ou_auto_admin_role(app, ou1, admin, user_with_auto_admin_role, auto_admin_role):
    def test_user_listing_auto_admin_role(user):
        response = login(app, user, '/manage/')

        # users are not visible
        with pytest.raises(IndexError):
            response = response.click(href='users')

        # test user's role page
        response = app.get('/manage/users/%d/roles/' % admin.pk)
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 1
        key, checked, _ = options[0]
        assert checked
        assert key == str(ou1.pk)
        q = response.pyquery.remove_namespaces()
        # only role_ou1 is visible
        assert len(q('table tbody tr')) == 1
        assert q('table tbody tr').text() == auto_admin_role.name

        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        names = {elt.text for elt in q('table tbody td.name a')}
        assert names == {'Auto Admin Role'}

        # test role listing
        response = app.get('/manage/roles/')
        assert len(response.form.fields['search-ou']) == 1
        field = response.form['search-ou']
        options = field.options
        assert len(options) == 1
        key, checked, dummy = options[0]
        assert checked
        assert key == str(ou1.pk)
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        names = [elt.text for elt in q('table tbody td.name a')]
        assert set(names) == {'Auto Admin Role'}

        response.form.set('search-internals', True)
        response = response.form.submit()
        q = response.pyquery.remove_namespaces()
        assert len(q('table tbody tr')) == 1
        names = {elt.text for elt in q('table tbody td.name a')}
        assert set(names) == {'Auto Admin Role'}

    test_user_listing_auto_admin_role(user_with_auto_admin_role)


def test_manager_deactivate_user(app, admin, settings):
    default_ou = OU.objects.get()
    User.objects.create(username='foo', ou=default_ou, first_name='Foo', last_name='Bar')
    response = login(app, admin, '/manage/users/')
    response = response.click('Foo Bar')
    assert 'Deactivated on' not in response.text
    assert 'Suspend' in response.text
    form = response.forms['object-actions']
    response = form.submit('deactivate')
    assert 'Deactivated on' in response.text
    assert 'by global admin' in response.text
    assert 'Activate' in response.text


def test_manager_search_user(app, superuser, admin, simple_role, settings):
    default_ou = OU.objects.get()
    User.objects.create(username='user1', ou=default_ou)
    User.objects.create(username='foobar', is_superuser=True, ou=default_ou)
    response = login(app, admin, '/manage/users/')

    # search without anything specified returns every user
    form = response.forms['search-form']
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 4
    names = {elt.text for elt in query('table tbody td.username')}
    assert names == {'admin', 'foobar', 'user1', 'superuser'}

    # search a non matching string returns nothing
    response = app.get('/manage/users/')
    form = response.forms['search-form']
    form.set('search-text', 'unkown')
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 0

    # search a string matching exactly a username returns this user
    response = app.get('/manage/users/')
    form = response.forms['search-form']
    form.set('search-text', 'superuser')
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 1
    assert query('table tbody td.username')[0].text == 'superuser'

    # search a string matching partially a username returns this user
    response = app.get('/manage/users/')
    form = response.forms['search-form']
    form.set('search-text', 'super')
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 1
    assert query('table tbody td.username')[0].text == 'superuser'

    # check only_superusers checkbox
    response = app.get('/manage/users/')
    form = response.forms['search-form']
    form.set('search-only_superusers', True)
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 2
    names = {elt.text for elt in query('table tbody td.username')}
    assert names == {'superuser', 'foobar'}

    # search a string and check only_superuser
    response = app.get('/manage/users/')
    form = response.forms['search-form']
    form.set('search-text', 'super')
    form.set('search-only_superusers', True)
    response = form.submit()
    query = response.pyquery.remove_namespaces()
    assert len(query('table tbody td.username')) == 1
    assert query('table tbody td.username')[0].text == 'superuser'


def test_manager_site_export(app, superuser):
    response = login(app, superuser, '/manage/site-export/')
    assert 'roles' in response.json
    assert 'ous' in response.json


def test_manager_site_export_forbidden(app, simple_user):
    login(app, simple_user)
    app.get('/manage/site-export/', status=403)


def test_manager_site_import(app, db, superuser):
    site_import = login(app, superuser, '/manage/site-import/')
    form = site_import.form
    site_export = {
        'roles': [
            {
                'description': '',
                'service': None,
                'name': 'basic',
                'attributes': [],
                'ou': {
                    'slug': 'default',
                    'uuid': 'ba60d9e6c2874636883bdd604b23eab2',
                    'name': 'Collectivit\u00e9 par d\u00e9faut',
                },
                'external_id': '',
                'slug': 'basic',
                'uuid': '6eb7bbf64bf547119120f925f0e560ac',
            }
        ]
    }
    form['site_json'] = Upload(
        'site_export.json', force_bytes(json.dumps(site_export).encode('ascii')), 'application/octet-stream'
    )
    res = form.submit()
    assert res.status_code == 302
    assert Role.objects.get(slug='basic')


def test_manager_site_import_error(app, db, superuser):
    site_import = login(app, superuser, '/manage/site-import/')
    form = site_import.form
    site_export = {
        'roles': [
            {
                'description': '',
                'service': None,
                'name': 'basic',
                'attributes': [],
                'ou': {'slug': 'unkown-ou', 'uuid': 'ba60d9e6c2874636883bdd604b23eab2', 'name': 'unkown ou'},
                'external_id': '',
                'slug': 'basic',
                'uuid': '6eb7bbf64bf547119120f925f0e560ac',
            }
        ]
    }
    form['site_json'] = Upload(
        'site_export.json', force_bytes(json.dumps(site_export).encode('ascii')), 'application/octet-stream'
    )
    res = form.submit()
    assert res.status_code == 200
    assert 'missing Organizational Unit' in res.text
    with pytest.raises(Role.DoesNotExist):
        Role.objects.get(slug='basic')

    form['site_json'] = Upload('site_export.json', force_bytes(json.dumps([])), 'application/octet-stream')
    res = form.submit()
    assert res.status_code == 200


def test_manager_site_import_forbidden(app, simple_user):
    login(app, simple_user)
    app.get('/manage/site-import/', status=403)


def test_manager_homepage_import_export(superuser, app):
    manager_home_page = login(app, superuser, reverse('a2-manager-homepage'))
    assert 'site-import' in manager_home_page.text
    assert 'site-export' in manager_home_page.text


def test_manager_homepage_import_export_hidden(admin, app):
    manager_home_page = login(app, admin, reverse('a2-manager-homepage'))
    assert 'site-import' not in manager_home_page.text
    assert 'site-export' not in manager_home_page.text


def test_manager_homepage_sidebar_title(app, simple_user, admin):
    user_admin_role = Role.objects.get(slug='_a2-manager-of-users')
    simple_user.roles.add(user_admin_role)
    manager_homepage = login(app, simple_user, reverse('a2-manager-homepage'))
    assert 'Configuration & technical information' not in manager_homepage
    logout(app)

    manager_homepage = login(app, admin, reverse('a2-manager-homepage'))
    assert 'Configuration & technical information' in manager_homepage


def test_manager_ou(app, superuser_or_admin, ou1):
    manager_home_page = login(app, superuser_or_admin, reverse('a2-manager-homepage'))
    ou_homepage = manager_home_page.click(href='organizational-units')
    assert {text_content(e) for e in ou_homepage.pyquery('td.name')} == {'OU1', 'Default organizational unit'}
    assert [x.text for x in ou_homepage.pyquery('td.slug')] == ['default', 'ou1']

    # add a new ou
    add_ou_page = ou_homepage.click('Add')
    add_ou_page.form.set('name', 'ou2')
    ou_homepage = add_ou_page.form.submit().follow()
    ou2 = OU.objects.get(name='ou2')
    assert {text_content(e) for e in ou_homepage.pyquery('td.name')} == {
        'OU1',
        'Default organizational unit',
        'ou2',
    }
    assert len(ou_homepage.pyquery('tr[data-pk="%s"] td.default span.true' % ou2.pk)) == 0
    assert len(ou_homepage.pyquery('tr td a[href="%s"]' % ou2.get_absolute_url())) == 1

    ou2_edit_page = app.get(reverse('a2-manager-ou-edit', kwargs={'pk': ou2.pk}))
    ou2_edit_page.form.set('default', True)

    ou2_edit_page.form.submit().follow()
    ou_homepage = manager_home_page.click(href='organizational-units')
    assert len(ou_homepage.pyquery('tr[data-pk="%s"] td.default span.true' % ou2.pk)) == 1
    assert len(ou_homepage.pyquery('tr td a[href="%s"]' % ou2.get_absolute_url())) == 1

    # FIXME: table lines are not clickable as they do not contain an anchor
    # default ou cannot be deleted
    ou2_detail_page = app.get(reverse('a2-manager-ou-detail', kwargs={'pk': ou2.pk}))
    assert ou2_detail_page.pyquery('a.disabled').text() == 'Delete'

    # but ou1 can be deleted
    ou1_detail_page = app.get(reverse('a2-manager-ou-detail', kwargs={'pk': ou1.pk}))
    ou1_delete_page = ou1_detail_page.click('Delete')
    ou_homepage = ou1_delete_page.form.submit().follow()
    assert {text_content(e) for e in ou_homepage.pyquery('td.name')} == {'Default organizational unit', 'ou2'}

    # remake old default ou the default one
    old_default = OU.objects.get(name__contains='Default')
    old_default_detail_page = app.get(reverse('a2-manager-ou-detail', kwargs={'pk': old_default.pk}))
    assert not old_default_detail_page.pyquery('input[name="default"][checked="checked"]')
    old_default_edit_page = old_default_detail_page.click('Edit')
    old_default_edit_page.form.set('default', True)
    old_default_detail_page = old_default_edit_page.form.submit().follow()
    # check detail page has changed
    assert old_default_detail_page.pyquery('input[name="default"][checked="checked"]')
    # check ou homepage has changed too
    ou_homepage = old_default_detail_page.click('Organizational unit')
    assert {text_content(e) for e in ou_homepage.pyquery('td.name')} == {'Default organizational unit', 'ou2'}
    assert len(ou_homepage.pyquery('span.true')) == 1
    assert len(ou_homepage.pyquery('tr[data-pk="%s"] td.default span.true' % ou2.pk)) == 0
    assert len(ou_homepage.pyquery('tr[data-pk="%s"] td.default span.true' % old_default.pk)) == 1

    # edit ou slug
    assert OU.objects.get(name='ou2').slug == 'ou2'
    ou2_detail_page = app.get(reverse('a2-manager-ou-edit', kwargs={'pk': ou2.pk}))
    form = ou2_detail_page.form
    assert 'slug' in form.fields
    form.set('slug', 'new-ou2-slug')
    form.submit().follow()
    assert OU.objects.get(name='ou2').slug == 'new-ou2-slug'


def test_return_on_logout(superuser, app):
    '''Verify we will return to /manage/ after logout/login cycle'''
    manager_home_page = login(app, superuser, reverse('a2-manager-homepage'))
    response = manager_home_page.click(href='logout').maybe_follow()
    assert response.request.query_string == 'next=/manage/'


def test_roles_widget(admin, app, db):
    from authentic2.manager.forms import ChooseRoleForm

    login(app, admin, '/manage/')
    cassis = OU.objects.create(name='Cassis')
    la_bedoule = OU.objects.create(name='La Bédoule')
    cuges = OU.objects.create(name='Cuges')
    Role.objects.create(ou=cassis, name='Administrateur')
    Role.objects.create(ou=la_bedoule, name='Administrateur')
    Role.objects.create(ou=cuges, name='Administrateur')

    form = ChooseRoleForm(request=None)
    assert form.as_p()
    field_id = form.fields['role'].widget.build_attrs({})['data-field_id']
    url = reverse('django_select2-json')
    response = app.get(url, params={'field_id': field_id, 'term': 'Admin'})
    assert len(response.json['results']) == 3
    response = app.get(url, params={'field_id': field_id, 'term': 'Admin cass'})
    assert len(response.json['results']) == 1
    assert response.json['results'][0]['text'] == 'Cassis - Administrateur'
    response = app.get(url, params={'field_id': field_id, 'term': force_str('Admin édou')})
    assert len(response.json['results']) == 1
    assert response.json['results'][0]['text'] == 'La Bédoule - Administrateur'


def test_roles_for_change_widget(admin, app, db):
    from authentic2.manager.forms import RoleParentForm

    login(app, admin, '/manage/')
    Role.objects.create(name='admin 1')
    Role.objects.create(name='user 1')

    form = RoleParentForm(request=None)
    assert form.as_p()
    field_id = form.fields['role'].widget.build_attrs({})['data-field_id']
    url = reverse('django_select2-json')
    response = app.get(url, params={'field_id': field_id, 'term': 'admin'})
    assert len(response.json['results']) == 1
    response = app.get(url, params={'field_id': field_id, 'term': '1'})
    assert len(response.json['results']) == 2
    response = app.get(url, params={'field_id': field_id, 'term': 'user 1'})
    assert len(response.json['results']) == 1


def test_manager_ajax_form_view_mixin_response(superuser_or_admin, app):
    app.set_user(superuser_or_admin.username)
    resp = app.get('/manage/roles/add/', xhr=True, status=200)
    assert resp.content_type == 'application/json'
    assert resp.json['content']


def test_manager_role_username_column(app, admin, simple_role):
    login(app, admin, '/manage/')

    resp = app.get('/manage/roles/%s/' % simple_role.id)
    assert resp.html.find('th', {'class': 'orderable username'})

    ou = get_default_ou()
    ou.show_username = False
    ou.save()

    resp = app.get('/manage/roles/%s/' % simple_role.id)
    assert not resp.html.find('th', {'class': 'asc orderable username'})


def test_manager_role_admin_permissions(app, simple_user, admin, simple_role):
    admin_role = simple_role.get_admin_role()
    simple_user.roles.add(admin_role)
    login(app, simple_user, '/manage/')

    # user can view users
    response = app.get('/manage/users/')
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 2
    assert {e.text for e in q('table tbody td.username')} == {'admin', 'user'}

    # user can view administered roles
    response = app.get('/manage/roles/')
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 1
    assert q('table tbody td.name').text() == 'simple role'

    # user can add members
    response = app.get('/manage/roles/%s/' % simple_role.pk)
    form = response.forms['add-member']
    form['user_or_role'].force_value('user-%s' % admin.pk)
    response = form.submit().follow()
    assert simple_role in admin.roles.all()

    # user can delete members
    q = response.pyquery.remove_namespaces()
    assert q('table tbody tr td .icon-remove-sign')
    token = str(response.context['csrf_token'])
    params = {'action': 'remove', 'user_or_role': 'user-%s' % admin.pk, 'csrfmiddlewaretoken': token}
    app.post('/manage/roles/%s/' % simple_role.pk, params=params, headers={'Referer': 'https://testserver/'})
    assert simple_role not in admin.roles.all()

    # user can act on role inheritance
    role = Role.objects.create(name='test_role')
    view_role_perm = Permission.objects.create(
        operation=get_operation(VIEW_OP), target_ct=ContentType.objects.get_for_model(Role), target_id=role.pk
    )
    simple_role.permissions.add(view_role_perm)
    simple_user.roles.add(simple_role)
    admin.roles.add(role)

    response = app.get('/manage/roles/%s/children/' % simple_role.pk)
    token = str(response.context['csrf_token'])
    params = {'action': 'add', 'role': role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/roles/%s/children/' % simple_role.pk,
        params=params,
        headers={'Referer': 'https://testserver/'},
    )
    assert role in simple_role.children()

    params = {'action': 'remove', 'role': role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/roles/%s/children/' % simple_role.pk,
        params=params,
        headers={'Referer': 'https://testserver/'},
    )
    assert role not in simple_role.children()

    response = app.get('/manage/roles/%s/parents/' % role.pk)
    token = str(response.context['csrf_token'])
    params = {'action': 'add', 'role': simple_role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/roles/%s/parents/' % role.pk, params=params, headers={'Referer': 'https://testserver/'}
    )
    assert simple_role in role.parents()

    params = {'action': 'remove', 'role': simple_role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/roles/%s/parents/' % role.pk, params=params, headers={'Referer': 'https://testserver/'}
    )
    assert simple_role not in role.parents()

    # user can add role as a member through role members form
    response = app.get('/manage/roles/%s/' % simple_role.pk)
    form = response.forms['add-member']
    form['user_or_role'].force_value('role-%s' % role.pk)
    response = form.submit().follow()
    assert role in simple_role.children()

    # user can delete role members
    q = response.pyquery.remove_namespaces()
    assert q('table tbody tr td .icon-remove-sign')
    token = str(response.context['csrf_token'])
    params = {'action': 'remove', 'user_or_role': 'role-%s' % role.pk, 'csrfmiddlewaretoken': token}
    app.post('/manage/roles/%s/' % simple_role.pk, params=params, headers={'Referer': 'https://testserver/'})
    assert role not in simple_role.children()

    # try to add arbitrary role
    admin_role = Role.objects.get(slug='_a2-manager')
    response = app.get('/manage/roles/%s/parents/' % role.pk)
    token = str(response.context['csrf_token'])
    params = {'action': 'add', 'role': admin_role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/roles/%s/parents/' % simple_role.pk,
        params=params,
        headers={'Referer': 'https://testserver/'},
    )
    assert admin_role not in role.parents()

    # user roles view works
    response = app.get('/manage/users/%s/roles/' % admin.pk)
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 1
    assert q('table tbody td.name').text() == 'simple role'

    token = str(response.context['csrf_token'])
    params = {'action': 'add', 'role': simple_role.pk, 'csrfmiddlewaretoken': token}
    response = app.post(
        '/manage/users/%s/roles/' % admin.pk, params=params, headers={'Referer': 'https://testserver/'}
    )
    assert simple_role in admin.roles.all()

    app.get('/manage/roles/add/', status=403)
    app.get('/manage/roles/%s/edit/' % simple_role.pk, status=403)
    app.get('/manage/roles/%s/delete/' % simple_role.pk, status=403)


def test_manager_widget_fields_validation(app, simple_user, simple_role):
    '''Verify that fields corresponding to widget implement queryset restrictions.'''
    from authentic2.manager.forms import (
        ChooseRoleForm,
        ChooseUserForm,
        ChooseUserRoleForm,
        RoleParentForm,
        RolesForm,
        UsersForm,
    )

    error_message = 'Select a valid choice'

    class DummyRequest:
        user = simple_user

    request = DummyRequest()

    visible_role = Role.objects.create(name='visible_role', ou=simple_user.ou)
    visible_user = User.objects.create(username='visible_user', ou=simple_user.ou)
    forbidden_role = Role.objects.create(name='forbidden_role', ou=simple_user.ou)
    forbidden_user = User.objects.create(username='forbidden_user', ou=simple_user.ou)

    view_role_perm = Permission.objects.create(
        operation=get_operation(VIEW_OP),
        target_ct=ContentType.objects.get_for_model(Role),
        target_id=visible_role.pk,
    )
    view_user_perm = Permission.objects.create(
        operation=get_operation(VIEW_OP),
        target_ct=ContentType.objects.get_for_model(User),
        target_id=visible_user.pk,
    )
    simple_role.permissions.add(view_role_perm)
    simple_role.permissions.add(view_user_perm)
    simple_user.roles.add(simple_role)

    form = ChooseUserForm(request=request, data={'user': visible_user.pk, 'action': 'add'})
    assert form.is_valid()
    form = ChooseUserForm(request=request, data={'user': forbidden_user.pk, 'action': 'add'})
    assert error_message in form.errors['user'][0]

    form = ChooseRoleForm(request=request, data={'role': visible_role.pk, 'action': 'add'})
    assert form.is_valid()
    form = ChooseRoleForm(request=request, data={'role': forbidden_role.pk, 'action': 'add'})
    assert error_message in form.errors['role'][0]

    form = UsersForm(request=request, data={'users': [visible_user.pk]})
    assert form.is_valid()
    form = UsersForm(request=request, data={'users': [forbidden_user.pk]})
    assert error_message in form.errors['users'][0]

    form = RolesForm(request=request, data={'roles': [visible_role.pk]})
    assert form.is_valid()
    form = RolesForm(request=request, data={'roles': [forbidden_role.pk]})
    assert error_message in form.errors['roles'][0]

    # For those we need manage_members permission
    form = RoleParentForm(request=request, data={'role': visible_role.pk, 'action': 'add'})
    assert error_message in form.errors['role'][0]

    form = ChooseUserRoleForm(request=request, data={'role': visible_role.pk, 'action': 'add'})
    assert error_message in form.errors['role'][0]

    change_role_perm = Permission.objects.create(
        operation=get_operation(MANAGE_MEMBERS_OP),
        target_ct=ContentType.objects.get_for_model(Role),
        target_id=visible_role.pk,
    )
    simple_role.permissions.add(change_role_perm)
    del simple_user._rbac_perms_cache

    form = RoleParentForm(request=request, data={'role': visible_role.pk, 'action': 'add'})
    assert form.is_valid()

    form = ChooseUserRoleForm(request=request, data={'role': visible_role.pk, 'action': 'add'})
    assert form.is_valid()


@pytest.mark.parametrize('relation', ['children', 'parents'])
def test_manager_role_inheritance_list(app, admin, simple_role, ou1, relation):
    first_role = Role.objects.create(name='first_role', ou=simple_role.ou)
    second_role = Role.objects.create(name='second_role', ou=simple_role.ou)
    third_role = Role.objects.create(name='third_role', ou=ou1)

    if relation == 'children':
        simple_role.add_child(first_role)
        first_role.add_child(second_role)
    elif relation == 'parents':
        simple_role.add_parent(first_role)
        first_role.add_parent(second_role)

    response = login(app, admin)
    response = app.get('/manage/roles/%s/%s/' % (simple_role.pk, relation))
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 3
    assert {e.text_content() for e in q('table tbody td.name')} == {
        first_role.name,
        second_role.name,
        third_role.name,
    }

    row = q('table tbody tr')[0]
    name, ou, via, member = row.getchildren()
    assert name.text_content() == 'first_role'
    assert ou.text_content() == 'Default organizational unit'
    assert not via.text_content()
    member = member.find('input')
    assert member.checked
    assert member.attrib['class'] == 'role-member'

    row = q('table tbody tr')[1]
    name, ou, via, member = row.getchildren()
    assert name.text_content() == 'second_role'
    assert ou.text_content() == 'Default organizational unit'
    assert via.text_content() == 'first_role'
    member = member.find('input')
    assert not member.checked
    assert member.attrib['class'] == 'role-member indeterminate'

    row = q('table tbody tr')[2]
    name, ou, via, member = row.getchildren()
    assert name.text_content() == 'third_role'
    assert ou.text_content() == 'OU1'
    assert not via.text_content()
    member = member.find('input')
    assert not member.checked
    assert member.attrib['class'] == 'role-member'


def test_manager_role_inheritance_list_search_permission(app, admin, simple_user, simple_role, ou1):
    visible_role = Role.objects.create(name='visible_role', ou=simple_user.ou)
    visible_role_2 = Role.objects.create(name='visible_role_2', ou=ou1)
    Role.objects.create(name='invisible_role', ou=simple_user.ou)
    admin_of_simple_role = simple_role.get_admin_role()

    admin_of_simple_role.members.add(simple_user)
    for role in (visible_role, visible_role_2):
        view_role_perm = Permission.objects.create(
            operation=get_operation(VIEW_OP),
            target_ct=ContentType.objects.get_for_model(Role),
            target_id=role.pk,
        )
        simple_role.permissions.add(view_role_perm)
    simple_user.roles.add(simple_role)

    response = login(app, simple_user, '/manage/roles/')

    # all visible roles are shown, except current role
    response = app.get('/manage/roles/%s/children/' % simple_role.pk)
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 2
    assert {e.text_content() for e in q('table tbody td.name')} == {visible_role.name, visible_role_2.name}

    # filter by ou
    response.form['search-ou'] = ou1.pk
    response = response.form.submit()
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 1
    assert {e.text_content() for e in q('table tbody td.name')} == {visible_role_2.name}

    # filter by name
    response.form['search-text'] = '2'
    response.form['search-ou'] = 'all'
    response = response.form.submit()
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 1
    assert {e.text_content() for e in q('table tbody td.name')} == {visible_role_2.name}

    # all roles with manage_members permissions are shown
    response = app.get('/manage/roles/%s/parents/' % visible_role.pk)
    q = response.pyquery.remove_namespaces()
    assert len(q('table tbody tr')) == 1
    assert {e.text_content() for e in q('table tbody td.name')} == {simple_role.name}


def test_manager_service_search(app, admin, ou1):
    Service.objects.create(ou=ou1, slug='test', name='Test Service')
    Service.objects.create(ou=get_default_ou(), slug='example', name='Example Service')

    resp = login(app, admin, 'a2-manager-services')
    assert 'Test Service' in resp.text
    assert 'Example Service' in resp.text

    resp.form.set('search-text', 'example')
    resp = resp.form.submit()
    assert 'Test Service' not in resp.text
    assert 'Example Service' in resp.text

    resp.form.set('search-text', '')
    resp.form.set('search-ou', ou1.pk)
    resp = resp.form.submit()
    assert 'Test Service' in resp.text
    assert 'Example Service' not in resp.text


def test_manager_service_edition(app, admin):
    ou = get_default_ou()
    service = Service.objects.create(
        name='TestService1', slug='testservice1', ou=ou, home_url='https://foo.bar'
    )
    login(app, admin)
    Event.objects.all().delete()
    resp = app.get(reverse('a2-manager-service-settings-edit', kwargs={'service_pk': service.pk}))
    resp.form['slug'] = 'anewslug'
    resp.form.submit().follow()

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1
    assert evts[0].type.name == 'manager.service.edit'
    assert evts[0].message == 'Service "TestService1" : changing slug from "testservice1" to "anewslug"'


def test_manager_service_role_management(app, admin, simple_role):
    ou = get_default_ou()
    service = Service.objects.create(
        name='TestService1', slug='testservice1', ou=ou, home_url='https://foo.bar'
    )
    login(app, admin)
    Event.objects.all().delete()
    resp = app.get(reverse('a2-manager-service', kwargs={'service_pk': service.pk}))
    form = resp.forms[0]
    form['role'].options = [(str(simple_role.pk), False, simple_role.slug)]
    form['role'] = simple_role.pk
    resp = form.submit().follow()
    service.refresh_from_db()
    assert simple_role in service.authorized_roles.all()

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1
    assert evts[0].type.name == 'manager.service.role.add'
    assert evts[0].message == f'Service "TestService1" : add role "{simple_role.name}" ({simple_role.slug})'

    Event.objects.all().delete()

    form = resp.forms[0]
    form['role'].options = [(str(simple_role.pk), False, simple_role.slug)]
    form['role'] = simple_role.pk
    form['action'] = 'remove'
    resp = form.submit().follow()
    service.refresh_from_db()
    assert simple_role not in service.authorized_roles.all()

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1
    assert evts[0].type.name == 'manager.service.role.delete'
    assert (
        evts[0].message == f'Service "TestService1" : delete role "{simple_role.name}" ({simple_role.slug})'
    )


def test_manager_services_settings(app, admin):
    for setting in Setting.objects.filter_namespace('sso'):
        assert setting.value == ''

    resp = login(app, admin, 'a2-manager-services-settings')
    resp.form.submit()

    for setting in Setting.objects.filter_namespace('sso'):
        assert setting.value == ''

    resp = app.get(reverse('a2-manager-services-settings'))
    resp.form.set('sso:generic_service_home_url', 'https://www.example.com/')
    resp.form.set('sso:generic_service_logo_url', 'https://www.example.com/logo.png')
    resp.form.set('sso:generic_service_colour', '#dedede')
    resp.form.set('sso:generic_service_name', 'Some default name')
    resp.form.submit()

    assert Setting.objects.get(key='sso:generic_service_home_url').value == 'https://www.example.com/'
    assert Setting.objects.get(key='sso:generic_service_logo_url').value == 'https://www.example.com/logo.png'
    assert Setting.objects.get(key='sso:generic_service_colour').value == '#dedede'
    assert Setting.objects.get(key='sso:generic_service_name').value == 'Some default name'

    resp = app.get(reverse('a2-manager-services-settings'))
    resp.form.set('sso:generic_service_home_url', 'https://www2.example.com/')
    resp.form.set('sso:generic_service_logo_url', '')
    resp.form.set('sso:generic_service_name', 'Some other name')
    resp.form.submit()

    assert Setting.objects.get(key='sso:generic_service_home_url').value == 'https://www2.example.com/'
    assert Setting.objects.get(key='sso:generic_service_logo_url').value == ''
    assert Setting.objects.get(key='sso:generic_service_colour').value == '#dedede'
    assert Setting.objects.get(key='sso:generic_service_name').value == 'Some other name'


def test_manager_service_homepage_link(app, admin):
    login(app, admin)
    ou = get_default_ou()

    service = Service.objects.create(
        name='TestService1', slug='testservice1', ou=ou, home_url='https://foo.bar'
    )

    resp = app.get(reverse('a2-manager-service', kwargs={'service_pk': service.pk}))
    assert '<a href="https://foo.bar">(Homepage)</a>' in resp

    service2 = Service.objects.create(name='TestService2', slug='testservice2', ou=ou)
    resp = app.get(reverse('a2-manager-service', kwargs={'service_pk': service2.pk}))
    assert 'https://foo.bar' not in resp
    assert '<a href="https://foo.bar">(Homepage)</a>' not in resp


def test_manager_users_advanced_configuration_settings(app, admin):
    for setting in Setting.objects.filter_namespace('users'):
        if setting.key == 'users:backoffice_sidebar_template':
            assert setting.value == ''
        elif setting.key == 'users:can_change_email_address':
            assert setting.value is True
        else:
            raise Exception('Unknown users setting %s' % setting.key)  # pragma: no cover

    resp = login(app, admin, 'a2-manager-services-settings')
    resp.form.submit()

    for setting in Setting.objects.filter_namespace('sso'):
        assert setting.value == ''

    # test value is saved correctly
    resp = app.get(reverse('a2-manager-users-advanced-configuration'))
    resp.form.set('users:backoffice_sidebar_template', 'Foo {{ user }}')
    resp.form.submit()

    assert Setting.objects.get(key='users:backoffice_sidebar_template').value == 'Foo {{ user }}'
    assert Setting.objects.get(key='users:can_change_email_address').value is True

    resp = app.get(reverse('a2-manager-users-advanced-configuration'))

    # test form is prefilled with the right value
    assert resp.form['users:backoffice_sidebar_template'].value == 'Foo {{ user }}'
    assert resp.form['users:can_change_email_address'].checked

    resp.form.set('users:can_change_email_address', False)
    resp.form.submit()

    assert Setting.objects.get(key='users:can_change_email_address').value is False
    assert Setting.objects.get(key='users:backoffice_sidebar_template').value == 'Foo {{ user }}'

    resp = app.get(reverse('a2-manager-users-advanced-configuration'))
    assert not resp.form['users:can_change_email_address'].checked


def test_manager_menu_json(app, admin):
    expected = [
        {
            'label': 'Identity management',
            'slug': 'identity-management',
            'url': 'https://testserver/manage/',
            'sub': False,
        },
        {'label': 'Users', 'slug': 'users', 'url': 'https://testserver/manage/users/', 'sub': True},
        {'label': 'Roles', 'slug': 'roles', 'url': 'https://testserver/manage/roles/', 'sub': True},
    ]

    response = login(app, admin)
    response = app.get('/manage/menu.json')
    assert response.json == expected


def test_manager_empty_kebab(app, admin, simple_user):
    role_admin_roles = Role.objects.get(slug='_a2-manager-of-roles')

    simple_user.roles.add(role_admin_roles.pk)
    resp = login(app, simple_user, '/manage/users/')
    assert '"extra-actions-menu-opener"' not in resp
    logout(app)

    resp = login(app, admin, '/manage/users/')
    assert '"extra-actions-menu-opener"' in resp


def test_manager_select2(app, superuser):
    login(app, superuser)
    response = app.get(reverse('django_select2-json'), expect_errors=True)
    assert response.status_code == 400


def test_manager_role_administrator_role(app, admin, simple_role):
    admin_role = Role.objects.create(name='admin 1')
    login(app, admin, '/manage/')

    internal_admin_role = simple_role.get_admin_role()
    assert admin_role not in internal_admin_role.children()

    resp = app.get('/manage/roles/%s/add-admin-role/' % simple_role.id)
    resp.form['roles'].force_value(admin_role.id)
    resp.form.submit().follow()

    internal_admin_role.refresh_from_db()
    assert admin_role in internal_admin_role.children()

    resp = app.get('/manage/roles/%s/remove-admin-role/%s/' % (simple_role.id, admin_role.id))
    resp.form.submit().follow()  # confirm deletion

    internal_admin_role.refresh_from_db()
    assert admin_role not in internal_admin_role.children()


def test_manager_role_administrator_role_add_journal(app, admin, simple_role):
    admin_role = Role.objects.create(name='admin 1')
    login(app, admin, '/manage/')

    Event.objects.all().delete()
    resp = app.get('/manage/roles/%s/add-admin-role/' % simple_role.id)
    resp.form['roles'].force_value(admin_role.id)
    resp.form.submit().follow()

    evt = Event.objects.get()
    msg = evt.message
    assert msg == f'addition of role "{admin_role}" as administrator of role "{simple_role}"'
    Role.objects.get(pk=admin_role.pk).delete()
    evt = Event.objects.get()
    assert evt.message == msg

    Role.objects.get(pk=simple_role.pk).delete()
    evt = Event.objects.get()
    assert evt.message == msg


def test_manager_role_administrator_role_remove_journal(app, admin, simple_role):
    admin_role = Role.objects.create(name='admin 1')
    login(app, admin, '/manage/')

    resp = app.get('/manage/roles/%s/add-admin-role/' % simple_role.id)
    resp.form['roles'].force_value(admin_role.id)
    resp.form.submit().follow()

    Event.objects.all().delete()
    resp = app.get('/manage/roles/%s/remove-admin-role/%s/' % (simple_role.id, admin_role.id))
    resp.form.submit().follow()  # confirm deletion

    evt = Event.objects.get()
    msg = evt.message
    assert msg == f'removal of role "{admin_role}" as administrator of role "{simple_role}"'
    Role.objects.get(pk=admin_role.pk).delete()
    evt = Event.objects.get()
    assert evt.message == msg

    Role.objects.get(pk=simple_role.pk).delete()
    evt = Event.objects.get()
    assert evt.message == msg


def test_manager_role_administrator_user(app, admin, simple_role, simple_user):
    login(app, admin, '/manage/')

    internal_admin_role = simple_role.get_admin_role()
    assert simple_user not in internal_admin_role.members.all()

    resp = app.get('/manage/roles/%s/add-admin-user/' % simple_role.id)
    resp.form['users'].force_value(simple_user.id)
    resp.form.submit().follow()

    assert simple_user in internal_admin_role.members.all()

    resp = app.get('/manage/roles/%s/remove-admin-user/%s/' % (simple_role.id, simple_user.id))
    resp.form.submit().follow()  # confirm deletion
    assert simple_user not in internal_admin_role.members.all()


def test_manager_role_administrator_user_add_journal(app, admin, simple_role):
    some_user = User.objects.create(username='some_user', email='some@example.com', ou=get_default_ou())
    login(app, admin, '/manage/')

    Event.objects.all().delete()
    resp = app.get('/manage/roles/%s/add-admin-user/' % simple_role.id)
    resp.form['users'].force_value(some_user.id)
    resp.form.submit().follow()

    evt = Event.objects.get()
    msg = evt.message
    assert msg == f'addition of user "{some_user}" as administrator of role "{simple_role}"'

    Role.objects.get(pk=simple_role.pk).delete()
    evt = Event.objects.get()
    assert msg == evt.message

    User.objects.get(pk=some_user.pk).delete()
    evt = Event.objects.get()
    assert msg == evt.message


def test_manager_role_administrator_user_remove_journal(app, admin, simple_role):
    some_user = User.objects.create(username='some_user', email='some@example.com', ou=get_default_ou())
    login(app, admin, '/manage/')

    resp = app.get('/manage/roles/%s/add-admin-user/' % simple_role.id)
    resp.form['users'].force_value(some_user.id)
    resp.form.submit().follow()

    Event.objects.all().delete()
    resp = app.get('/manage/roles/%s/remove-admin-user/%s/' % (simple_role.id, some_user.id))
    resp.form.submit().follow()  # confirm deletion

    evt = Event.objects.get()
    msg = evt.message
    assert msg == f'removal of user "{some_user}" as administrator of role "{simple_role}"'

    Role.objects.get(pk=simple_role.pk).delete()
    evt = Event.objects.get()
    assert msg == evt.message

    User.objects.get(pk=some_user.pk).delete()
    evt = Event.objects.get()
    assert msg == evt.message
