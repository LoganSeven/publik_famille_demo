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


import csv
import datetime
import re
import time
import unittest.mock
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.html import escape
from django.utils.timezone import now
from mellon.models import Issuer, UserSAMLIdentifier
from webtest import Upload

from authentic2.a2_rbac.models import VIEW_OP
from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role
from authentic2.a2_rbac.utils import get_default_ou, get_operation, get_search_user_perm, get_view_user_perm
from authentic2.apps.journal.models import Event
from authentic2.custom_user.models import User
from authentic2.manager import user_import
from authentic2.models import Attribute, AttributeValue, Setting, UserExternalId
from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient

from .utils import get_link_from_mail, login, logout


def visible_users(response):
    return {elt.text for elt in response.pyquery('td.username')}


def test_create_user(app, superuser):
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('username', 'john.doe')
    response.form.set('email', 'john.doe@example.com')
    response.form.set('first_name', 'Jôhn')
    response.form.set('last_name', 'Döe')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2
    user = User.objects.exclude(id=superuser.id).get()
    assert user.ou == get_default_ou()
    assert user.username == 'john.doe'
    assert user.email == 'john.doe@example.com'
    assert user.first_name == 'Jôhn'
    assert user.last_name == 'Döe'
    assert user.check_password('1234Password!?')


def test_create_user_permission_denied(app, simple_user, ou1, ou2):
    ou1.get_admin_role().members.add(simple_user)
    response = login(app, simple_user, '/manage/users/%s/add/' % ou1.id)

    assert 'You are not authorized to see this page.' not in response.text

    response = app.get('/manage/users/%s/add/' % ou2.id, status=403)
    assert 'You are not authorized to see this page.' in response.text


def test_create_user_only_name(app, superuser):
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('first_name', 'Jôhn')
    response.form.set('last_name', 'Döe')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2


def test_create_user_only_email(app, superuser):
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('email', 'john.doe@example.com')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2


def test_create_user_only_username(app, superuser):
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('username', 'john.doe')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2


def test_create_user_no_identifier(app, superuser):
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=200)
    assert User.objects.count() == 1
    assert 'An account needs at least one identifier: ' in response


def test_create_user_username_is_unique(app, superuser, settings):
    settings.A2_USERNAME_IS_UNIQUE = True
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('username', 'john.doe')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2

    # try again
    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form.set('username', 'john.doe')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=200)
    assert User.objects.count() == 2
    assert 'This username is already in use' in response


def test_create_user_email_is_unique(app, superuser, settings):
    settings.A2_EMAIL_IS_UNIQUE = True
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('email', 'john.doe@example.com')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2

    # try again
    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form.set('email', 'john.doe@example.com')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=200)
    assert User.objects.count() == 2
    assert 'This email address is already in use' in response


def test_create_user_phone_is_unique(app, superuser, settings, phone_activated_authn):
    settings.A2_PHONE_IS_UNIQUE = True
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 1
    response.form.set('username', 'john.doe')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622332233')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 2

    # try again
    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form.set('username', 'john.doe2')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622332233')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=200)
    assert User.objects.count() == 2
    assert 'This phone number identifier is already used.' in response


def test_create_user_ou_phone_is_unique(app, superuser, settings, phone_activated_authn, ou1, ou2, user_ou2):
    settings.A2_PHONE_IS_UNIQUE = False
    ou2.phone_is_unique = ou1.phone_is_unique = True
    ou1.save()
    ou2.save()
    Attribute.objects.update(required=False)
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert User.objects.count() == 2
    response.form.set('ou', ou1.id)
    response = response.form.submit().follow()
    response.form.set('username', 'john.doe')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622332233')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)
    assert User.objects.count() == 3

    # try again, same ou -> failure
    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form.set('ou', ou1.id)
    response = response.form.submit().follow()
    response.form.set('username', 'john.doe2')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622332233')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=200)
    assert User.objects.count() == 3
    assert 'This phone number identifier is already used within organizational unit OU1.' in response

    # try again, different ou -> ok
    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form.set('ou', ou2.id)
    response = response.form.submit().follow()
    response.form.set('username', 'john.doe2')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622332233')
    response.form.set('password1', '1234Password!?')
    response.form.set('password2', '1234Password!?')
    response.form.set('send_password_reset', False)
    response.form.submit(status=302)
    assert User.objects.count() == 4

    user_ou2.attributes.phone = '+33622222222'
    user_ou2.save()

    # check uniqueness constraints respected at edit time
    new_user_ou2 = User.objects.get(username='john.doe2')
    response = app.get(f'/manage/users/{new_user_ou2.pk}/edit/')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622222222')
    response = response.form.submit(status=200)
    assert 'This phone number identifier is already used within organizational unit OU2.' in response
    new_user_ou2.refresh_from_db()
    assert new_user_ou2.attributes.phone == '+33622332233'  # unchanged

    response = app.get(f'/manage/users/{new_user_ou2.pk}/edit/')
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0622222224')
    response.form.submit(status=302)
    new_user_ou2.refresh_from_db()
    assert new_user_ou2.attributes.phone == '+33622222224'  # changed


def test_create_user_no_password(app, superuser):
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    response.form.set('first_name', 'John')
    response.form.set('last_name', 'Doe')
    response.form.set('generate_password', False)
    response.form.set('password1', '')
    response.form.set('password2', '')
    response.form.set('send_password_reset', False)
    response = response.form.submit(status=302)

    user = User.objects.filter(is_superuser=False).get()
    assert user.has_usable_password()


def test_create_user_choose_ou(app, superuser, simple_user, ou1, ou2):
    response = login(app, superuser, '/manage/users/')
    response = response.click('Add user')
    assert 'Choose organizational unit' in response.text

    response = response.form.submit()
    assert str(get_default_ou().pk) in response.url

    response = app.get('/manage/users/')
    response = response.click('Add user')
    response.form['ou'] = ou1.pk
    response = response.form.submit()
    assert str(ou1.pk) in response.url

    logout(app)
    view_user_role = Role.objects.create(name='view_user', ou=simple_user.ou)
    view_user_role.permissions.add(get_view_user_perm())
    simple_user.roles.add(view_user_role)
    response = login(app, simple_user, '/manage/users/')
    assert response.pyquery.find('a#add-user-btn.disabled')


def test_manager_user_change_email(app, superuser_or_admin, simple_user, mailoutbox):
    ou = get_default_ou()
    ou.validate_emails = True
    ou.save()

    NEW_EMAIL = 'john.doe@example.com'

    assert NEW_EMAIL != simple_user.email

    response = login(
        app,
        superuser_or_admin,
        reverse('a2-manager-user-by-uuid-detail', kwargs={'slug': str(simple_user.uuid)}),
    )
    assert 'Change user email' in response.text
    # cannot click it's a submit button :/
    response = app.get(
        reverse('a2-manager-user-by-uuid-change-email', kwargs={'slug': str(simple_user.uuid)})
    )
    assert response.form['new_email'].value == simple_user.email
    response.form.set('new_email', NEW_EMAIL)
    assert len(mailoutbox) == 0
    response = response.form.submit().follow()
    assert 'A mail was sent to john.doe@example.com to verify it.' in response.text
    assert 'Change user email' in response.text
    # cannot click it's a submit button :/
    assert len(mailoutbox) == 1
    assert simple_user.email in mailoutbox[0].body
    assert NEW_EMAIL in mailoutbox[0].body

    # logout
    app.session.flush()

    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link).maybe_follow()
    assert 'your request for changing your email for john.doe@example.com is successful' in response.text
    simple_user.refresh_from_db()
    assert simple_user.email == NEW_EMAIL


def test_manager_user_change_email_no_change(app, superuser_or_admin, simple_user, mailoutbox):
    ou = get_default_ou()
    ou.validate_emails = True
    ou.save()

    NEW_EMAIL = 'john.doe@example.com'

    assert NEW_EMAIL != simple_user.email

    response = login(
        app,
        superuser_or_admin,
        reverse('a2-manager-user-by-uuid-detail', kwargs={'slug': str(simple_user.uuid)}),
    )
    assert 'Change user email' in response.text
    # cannot click it's a submit button :/
    response = app.get(
        reverse('a2-manager-user-by-uuid-change-email', kwargs={'slug': str(simple_user.uuid)})
    )
    assert response.form['new_email'].value == simple_user.email
    assert len(mailoutbox) == 0
    response = response.form.submit().follow()
    assert 'A mail was sent to john.doe@example.com to verify it.' not in response.text


def test_search_by_attribute(app, simple_user, admin):
    Attribute.objects.create(name='adresse', searchable=True, kind='string')

    simple_user.attributes.adresse = 'avenue du revestel'
    response = login(app, admin, '/manage/users/')

    # all users are visible
    assert visible_users(response) == {simple_user.username, admin.username}

    response.form['search-text'] = 'impasse'
    response = response.form.submit()
    # now all users are hidden
    assert not visible_users(response) & {simple_user.username, admin.username}

    response.form['search-text'] = 'avenue'
    response = response.form.submit()

    # now we see only simple_user
    assert visible_users(response) == {simple_user.username}

    simple_user.delete()
    response.form['search-text'] = 'avenue'
    response = response.form.submit()

    assert visible_users(response) == set()


def test_search_by_phone_local_number(app, simple_user, admin, settings):
    settings.DEFAULT_COUNTRY_CODE = '33'
    Attribute.objects.create(
        kind='phone_number', name='phone', label='Phone', required=False, searchable=True
    )

    simple_user.attributes.phone = '+33612345678'
    simple_user.save()

    response = login(app, admin, '/manage/users/')

    # all users are visible
    assert visible_users(response) == {simple_user.username, admin.username}

    response.form['search-text'] = '9876543210'
    response = response.form.submit()
    # now all users are hidden
    assert not visible_users(response) & {simple_user.username, admin.username}

    response.form['search-text'] = '0612345678'
    response = response.form.submit()
    # now we see only simple_user
    assert visible_users(response) == {simple_user.username}

    response.form['search-text'] = '+33612345678'
    response = response.form.submit()
    assert visible_users(response) == {simple_user.username}

    simple_user.delete()
    response.form['search-text'] = '0612345678'
    response = response.form.submit()
    assert visible_users(response) == set()


def test_export_csv(settings, app, superuser, django_assert_num_queries):
    AT_COUNT = 30
    USER_COUNT = 2000
    DEFAULT_BATCH_SIZE = 1000

    ats = [Attribute(name='at%s' % i, label='At%s' % i, kind='string') for i in range(AT_COUNT)]
    Attribute.objects.bulk_create(ats)

    ats = list(Attribute.objects.all())
    users = [User(username='user%s' % i) for i in range(USER_COUNT)]
    User.objects.bulk_create(users)
    users = list(User.objects.filter(username__startswith='user'))

    ContentType.objects.get_for_model(User)
    atvs = []
    for i in range(USER_COUNT):
        atvs.extend(
            [
                AttributeValue(owner=users[i], attribute=ats[j], content='value-%s-%s' % (i, j))
                for j in range(AT_COUNT)
            ]
        )
    AttributeValue.objects.bulk_create(atvs)

    response = login(app, superuser, reverse('a2-manager-users'))
    settings.A2_CACHE_ENABLED = True
    user_count = User.objects.count()
    # queries should be batched to keep prefetching working without
    # overspending memory for the queryset cache, 4 queries by batches
    num_queries = int(4 * (user_count / DEFAULT_BATCH_SIZE + bool(user_count % DEFAULT_BATCH_SIZE)))
    # export task also perform one query to set trigram an another to get users count
    num_queries += 3
    with django_assert_num_queries(num_queries):
        response = response.click('CSV')

    url = response.url
    response = response.follow()
    assert 'Preparing CSV export file...' in response.text
    assert '<span id="progress">0</span>' in response.text

    response = response.click('Download CSV')
    table = list(csv.reader(response.text.splitlines()))
    assert len(table) == (user_count + 1)
    assert len(table[0]) == (15 + AT_COUNT)

    # ajax call returns 100% progress
    resp = app.get(url, xhr=True)
    assert resp.text == '100'


def test_export_csv_search(settings, app, superuser):
    users = [User(username='user%s' % i) for i in range(10)]
    User.objects.bulk_create(users)

    login(app, superuser)
    resp = app.get('/manage/users/?search-text=user1')
    resp = resp.click('CSV').follow()
    resp = resp.click('Download CSV')
    table = list(csv.reader(resp.text.splitlines()))
    assert len(table) == 3  # user1 and superuser match


def test_export_csv_disabled_attribute(settings, app, superuser):
    attr = Attribute.objects.create(name='attr', label='Attr', kind='string')
    attr_d = Attribute.objects.create(name='attrd', label='Attrd', kind='string')

    user = User.objects.create(username='user-foo')
    AttributeValue.objects.create(owner=user, attribute=attr, content='attr-value')
    AttributeValue.objects.create(owner=user, attribute=attr_d, content='attrd-value')

    attr_d.disabled = True
    attr_d.save()

    response = login(app, superuser, reverse('a2-manager-users'))
    settings.A2_CACHE_ENABLED = True
    response = response.click('CSV').follow()
    response = response.click('Download CSV')

    user_count = User.objects.count()
    table = list(csv.reader(response.text.splitlines()))
    assert len(table) == (user_count + 1)
    num_col = 15 + 1  # 1 is the number active attributes,
    # disabled attribute should not show up
    for line in table:
        assert len(line) == num_col


def test_export_csv_user_delete(settings, app, superuser):
    for i in range(10):
        User.objects.create(username='user-%s' % i)

    # users marked as deleted should not show up
    for user in User.objects.all()[0:3]:
        user.delete()

    response = login(app, superuser, reverse('a2-manager-users'))
    settings.A2_CACHE_ENABLED = True
    response = response.click('CSV').follow()
    response = response.click('Download CSV')
    table = list(csv.reader(response.text.splitlines()))
    # superuser + ten created users + csv header - three users marked as deteled
    assert len(table) == (1 + 10 + 1 - 3)


def test_export_csv_escape_formula(settings, app, superuser):
    User.objects.create(username='=1 + 2')

    login(app, superuser)
    resp = app.get('/manage/users/')
    resp = resp.click('CSV').follow()
    resp = resp.click('Download CSV')
    cells = [cell for row in csv.reader(resp.text.splitlines()) for cell in row]
    assert '=1 + 2' not in cells
    assert '\'=1 + 2' in cells


def test_user_table(app, admin, user_ou1, ou1):
    from authentic2.manager.utils import has_show_username

    # base state, username are shown
    response = login(app, admin, '/manage/users/')
    assert response.pyquery('td.username')

    # hide all usernames, from specific and general view
    OU.objects.update(show_username=False)
    has_show_username.cache.clear()

    response = app.get('/manage/users/')
    assert not response.pyquery('td.username')

    response = app.get('/manage/users/?search-ou=%s' % get_default_ou().id)
    assert not response.pyquery('td.username')

    response = app.get('/manage/users/?search-ou=%s' % ou1.id)
    assert not response.pyquery('td.username')

    # hide username except in OU1
    ou1.show_username = True
    ou1.save()
    has_show_username.cache.clear()

    response = app.get('/manage/users/')
    assert not response.pyquery('td.username')

    response = app.get('/manage/users/?search-ou=%s' % get_default_ou().id)
    assert not response.pyquery('td.username')

    response = app.get('/manage/users/?search-ou=%s' % ou1.id)
    assert response.pyquery('td.username')


def test_user_table_num_queries(
    app,
    admin,
    phone_activated_authn,
    db,
):
    for i in range(30):
        user = User.objects.create(
            first_name=f'Foo{i}',
            last_name=f'Bar{i}',
            email=f'foobar-{i}@example.com',
        )
        user.attributes.phone = f'+336112233{i:02d}'
        user.save()
    login(app, admin, '/')
    with CaptureQueriesContext(connection) as ctx:
        app.get('/manage/users/')
        assert len(ctx.captured_queries) == 23


def test_user_table_phone_authentication_active(
    app,
    admin,
    user_ou1,
    user_ou2,
    ou1,
    ou2,
    phone_activated_authn,
    db,
):
    user_ou2.attributes.phone = '+33999999999'
    user_ou1.phone_verified_on = None
    user_ou2.save()
    user_ou1.attributes.phone = '+33666666666'
    user_ou1.phone_verified_on = None
    user_ou1.save()
    admin.attributes.phone = '+32777777777'
    admin.phone_verified_on = now()
    admin.save()

    login(app, admin, '/')
    response = app.get('/manage/users/')
    assert response.pyquery('th a[href="?sort=phone_id"]').text() == 'Phone'

    response = app.get('/manage/users/?sort=phone_id')

    assert response.pyquery('tr')[1].get('data-pk') == str(admin.id)
    assert '+32777777777' in response.pyquery('tr')[1].text_content()
    assert response.pyquery('tr')[1].findall('td')[3].find('span').get('class') == 'verified'

    assert response.pyquery('tr')[2].get('data-pk') == str(user_ou1.id)
    assert '+33666666666' in response.pyquery('tr')[2].text_content()
    assert not response.pyquery('tr')[2].findall('td')[3].find('span')

    assert response.pyquery('tr')[3].get('data-pk') == str(user_ou2.id)
    assert '+33999999999' in response.pyquery('tr')[3].text_content()
    assert not response.pyquery('tr')[3].findall('td')[3].find('span')

    response = app.get('/manage/users/?sort=-phone_id')

    assert response.pyquery('tr')[1].get('data-pk') == str(user_ou2.id)
    assert '+33999999999' in response.pyquery('tr')[1].text_content()
    assert not response.pyquery('tr')[1].findall('td')[3].find('span')

    assert response.pyquery('tr')[2].get('data-pk') == str(user_ou1.id)
    assert '+33666666666' in response.pyquery('tr')[2].text_content()
    assert not response.pyquery('tr')[2].findall('td')[3].find('span')

    assert response.pyquery('tr')[3].get('data-pk') == str(admin.id)
    assert '+32777777777' in response.pyquery('tr')[3].text_content()
    assert response.pyquery('tr')[3].findall('td')[3].find('span').get('class') == 'verified'

    phone_activated_authn.accept_phone_authentication = False
    phone_activated_authn.save()
    response = app.get('/manage/users/')
    assert not response.pyquery('th a[href="?sort=phone_id"]')
    assert 'Phone' not in response.text

    phone_activated_authn.accept_phone_authentication = True
    phone_activated_authn.save()
    phone_activated_authn.phone_identifier_field.label = 'Burner phone'
    phone_activated_authn.phone_identifier_field.save()

    response = app.get('/manage/users/')
    assert response.pyquery('th a[href="?sort=phone_id"]').text() == 'Burner phone'


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp1252', 'iso-8859-15'])
def test_user_import(encoding, transactional_db, app, admin, ou1, admin_ou1, admin_ou2):
    Attribute.objects.create(name='phone', kind='phone_number', label='Numéro de téléphone')

    deleted_user = User.objects.create(
        email='john.doe@entrouvert.com', username='jdoe', first_name='John', last_name='doe'
    )
    deleted_user.delete()

    user_count = User.objects.count()

    assert Attribute.objects.count() == 3

    response = login(app, admin_ou1, '/manage/users/')
    Event.objects.all().delete()

    response = response.click('Import users')
    response.form.set(
        'import_file',
        Upload(
            'users.csv',
            '''email key verified,first_name,last_name,phone
tnoel@entrouvert.com,Thomas,Noël,0123456789
fpeters@entrouvert.com,Frédéric,Péters,+3281123456
john.doe@entrouvert.com,John,Doe,0910111213
x,x,x,x'''.encode(
                encoding
            ),
            'application/octet-stream',
        ),
    )
    response.form.set('encoding', encoding)
    response.form.set('ou', str(ou1.pk))
    response = response.form.submit()

    imports = list(user_import.UserImport.all())
    assert len(imports) == 1
    _import_uuid = response.location.split('/')[-2]
    _import = user_import.UserImport(uuid=_import_uuid)
    assert _import.exists()

    response = response.follow()

    response = response.forms['action-form'].submit(name='simulate')

    reports = list(_import.reports)
    assert len(reports) == 1
    uuid = reports[0].uuid

    response = response.follow()

    report_url = response.pyquery('tr[data-uuid="%s"]' % uuid).attr('data-url')
    ajax_resp = app.get(report_url, xhr=True)
    assert len(ajax_resp.pyquery('td')) == 5
    assert 'body' not in ajax_resp

    def assert_timeout(duration, wait_function):
        start = time.time()
        while True:
            result = wait_function()
            if result is not None:
                return result
            assert time.time() - start < duration, '%s timed out after %s seconds' % (wait_function, duration)
            time.sleep(0.001)

    def wait_finished():
        new_resp = response.click('Users Import')
        if new_resp.pyquery('tr[data-uuid="%s"] td.state' % uuid).text() == 'Finished':
            return new_resp

    simulate = reports[0]
    assert simulate.simulate

    response = assert_timeout(3, wait_finished)

    response = response.click(href=simulate.uuid)

    assert len(response.pyquery('table.main tbody tr')) == 5
    assert len(response.pyquery('table.main tbody tr.row-valid')) == 3
    assert len(response.pyquery('table.main tbody tr.row-invalid')) == 2

    assert len(response.pyquery('tr.row-errors')) == 0
    assert len(response.pyquery('tr.row-cells-errors')) == 1
    assert sum(bool(response.pyquery(td).text()) for td in response.pyquery('tr.row-cells-errors td li')) == 2
    assert 'Enter a valid email address' in response.pyquery('tr.row-cells-errors td.cell-email li').text()
    assert 'Enter a valid phone number' in response.pyquery('tr.row-cells-errors td.cell-phone li').text()

    assert User.objects.count() == user_count
    # nothing in journal when simulating
    assert not list(Event.objects.order_by('timestamp', 'id'))

    response = response.click('Users Import')
    response = response.forms['action-form'].submit(name='execute')

    execute = list(report for report in _import.reports if not report.simulate)[0]
    uuid = execute.uuid

    response = response.follow()
    response = assert_timeout(3, wait_finished)
    assert list(Event.objects.order_by('timestamp', 'id'))

    assert User.objects.count() == user_count + 3
    assert (
        User.objects.filter(
            email='tnoel@entrouvert.com',
            first_name='Thomas',
            last_name='Noël',
            attribute_values__content='+33123456789',
        ).count()
        == 1
    )
    assert (
        User.objects.filter(
            email='fpeters@entrouvert.com',
            first_name='Frédéric',
            last_name='Péters',
            attribute_values__content='+3281123456',
        ).count()
        == 1
    )
    assert (
        User.objects.filter(
            email='john.doe@entrouvert.com',
            first_name='John',
            last_name='Doe',
            attribute_values__content='+33910111213',
        ).count()
        == 1
    )

    # logout
    app.session.flush()
    response = login(app, admin_ou2, '/manage/users/')

    resp = app.get('/manage/users/import/', status=200)
    resp.mustcontain(no=[_import.uuid])
    app.get('/manage/users/import/%s/' % _import.uuid, status=403)
    app.get('/manage/users/import/%s/%s/' % (_import.uuid, simulate.uuid), status=403)
    app.get('/manage/users/import/%s/%s/' % (_import.uuid, execute.uuid), status=403)

    # logout
    app.session.flush()
    response = login(app, admin, '/manage/users/')

    resp = app.get('/manage/users/import/', status=200)
    resp.mustcontain(_import.uuid)
    app.get('/manage/users/import/%s/' % _import.uuid, status=200)
    app.get('/manage/users/import/%s/%s/' % (_import.uuid, simulate.uuid), status=200)
    app.get('/manage/users/import/%s/%s/' % (_import.uuid, execute.uuid), status=200)

    app.get('/manage/users/import/404/', status=404)
    app.get('/manage/users/import/%s/404/' % _import.uuid, status=404)


def test_user_import_legacy_encoding(transactional_db, app, admin, ou1, admin_ou1):
    response = login(app, admin, '/manage/users/')
    response = response.click('Import users')
    response.form.set(
        'import_file',
        Upload(
            'users.csv',
            '''email key verified,first_name,last_name,phone
tnoel@entrouvert.com,Thomas,Noël,0123456789
fpeters@entrouvert.com,Frédéric,Péters,+3281123456
john.doe@entrouvert.com,John,Doe,0910111213
x,x,x,x'''.encode(),
            'application/octet-stream',
        ),
    )
    response.form.set('encoding', 'utf-8-sig')
    response.form.set('ou', str(get_default_ou().pk))
    response = response.form.submit()

    imports = [i for i in user_import.UserImport.all()]
    # oops, utf-8 used to be supported. now it's utf-8-sig but imports may have
    # been created with utf-8 encoding and not executed yet
    with imports[0].meta_update as meta:
        meta['encoding'] = 'utf-8'  # not supported anymore

    response = response.follow()
    response = response.forms['action-form'].submit(name='simulate')


def test_su_permission(app, admin, simple_user):
    Event.objects.all().delete()
    resp = login(app, admin, '/manage/users/%s/' % simple_user.pk)
    assert len(resp.pyquery('button[name="su"]')) == 0
    assert app.get('/manage/users/%s/su/' % simple_user.pk, status=403)
    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 1  # password login


def test_su_superuser_post(app, app_factory, superuser, simple_user):
    Event.objects.all().delete()
    resp = login(app, superuser, '/manage/users/%s/' % simple_user.pk)
    assert len(resp.pyquery('button[name="su"]')) == 1
    su_resp = resp.forms['object-actions'].submit(name='su')

    new_app = app_factory()
    new_app.get(su_resp.location).maybe_follow()
    assert new_app.session['_auth_user_id'] == str(simple_user.pk)

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 2  # password login, su login
    assert evts[-1].data == {'how': 'su'}
    assert evts[-1].message == 'login using login as token'


def test_su_superuser_dialog(app, app_factory, superuser, simple_user):
    Event.objects.all().delete()
    resp = login(app, superuser, '/manage/users/%s/' % simple_user.pk)
    assert len(resp.pyquery('button[name="su"]')) == 1

    su_view_url = resp.pyquery('button[name="su"]')[0].get('data-url')

    resp = app.get(su_view_url)

    anchors = resp.pyquery('a#su-link')
    assert len(anchors) == 1

    su_url = anchors[0].get('href')

    new_app = app_factory()
    resp = new_app.get(su_url).maybe_follow()
    assert new_app.session['_auth_user_id'] == str(simple_user.pk)
    assert resp.pyquery('#a2-profile')
    assert resp.pyquery('.ui-name').text() == simple_user.get_full_name()

    evts = list(Event.objects.order_by('timestamp', 'id'))
    assert len(evts) == 3  # password login, su token, su login
    assert evts[-2].data == {'as_username': simple_user.username, 'as_userid': simple_user.id}
    assert evts[-2].message == f'login as token generated for "{simple_user.username}" (id={simple_user.id})'
    assert evts[-2].user == superuser
    assert evts[-1].message == 'login using login as token'
    assert evts[-1].data == {'how': 'su'}
    assert evts[-1].message == 'login using login as token'


def import_csv(csv_content, app):
    response = app.get('/manage/users/')
    response = response.click('Import users')
    index = [i for i in response.forms if 'import_file' in response.forms[i].fields][0]
    response.forms[index].set(
        'import_file', Upload('users.csv', csv_content.encode('utf-8'), 'application/octet-stream')
    )
    response.forms[index].set('encoding', 'utf-8-sig')
    response.forms[index].set('ou', str(get_default_ou().pk))
    response = response.forms[index].submit().follow()
    response = response.forms['action-form'].submit(name='execute').follow()

    start = time.time()
    response = response.click('Users Import')
    assert 'Encoding: Unicode (UTF-8)' in response.text
    assert 'Target Organizational Unit: Default organizational unit' in response.text

    while 'Running' in response.text:
        response = response.click('Users Import')
        assert time.time() - start < 3
        time.sleep(0.1)

    # report
    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    response = app.get(urls[0])
    return response


def check_import_csv_journal(url, expected_messages):
    evts = list(Event.objects.order_by('timestamp', 'id'))
    report_uuid = url.split('/')[-2]
    prefix = '<a href="%s">CSV user import %s</a> ' % (url, report_uuid)
    for evt in evts:
        assert evt.message.startswith(prefix)
    assert [evt.message[len(prefix) :] for evt in evts] == [escape(m) for m in expected_messages]


def test_user_import_attributes(transactional_db, app, admin):
    Attribute.objects.create(name='more', kind='string', label='Signe particulier')
    Attribute.objects.create(name='title', kind='title', label='Titre')
    Attribute.objects.create(name='bike', kind='boolean', label='Vélo')
    Attribute.objects.create(name='saintsday', kind='date', label='Fête')
    Attribute.objects.create(name='birthdate', kind='birthdate', label='Date de naissance')
    Attribute.objects.create(name='zip', kind='fr_postcode', label='Code postal (français)')
    Attribute.objects.create(name='phone', kind='phone_number', label='Numéro de téléphone')
    assert Attribute.objects.count() == 9
    user_count = User.objects.count()
    login(app, admin, '/manage/users/')

    csv_lines = [
        'email key verified,first_name,last_name,more,title,bike,saintsday,birthdate,zip,phone',
        'elliot@universalpictures.com,Elliott,Thomas,petit,Mr,True,2019-7-20,1972-05-26,75014,0123456789',
        'et@universalpictures.com,ET,the Extra-Terrestrial,long,??,False,1/2/3/4,0002-2-22,42,home',
    ]
    Event.objects.all().delete()
    response = import_csv('\n'.join(csv_lines), app)

    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    response = app.get(urls[0])
    assert 'Select a valid choice. ?? is not one of the available choices.' in response.text
    assert 'Enter a valid date.' in response.text
    assert 'birthdate must be in the past and greater or equal than 1900-01-01.' in response.text
    assert 'The value must be a valid french postcode' in response.text
    assert 'Enter a valid phone number' in response.text

    assert User.objects.count() == user_count + 1
    elliot = User.objects.filter(email='elliot@universalpictures.com')[0]
    assert elliot.attributes.values['more'].content == 'petit'
    assert elliot.attributes.values['title'].content == 'Mr'
    assert elliot.attributes.values['bike'].content == '1'
    assert elliot.attributes.values['saintsday'].content == '2019-07-20'
    assert elliot.attributes.values['birthdate'].content == '1972-05-26'
    assert elliot.attributes.values['zip'].content == '75014'
    assert elliot.attributes.values['phone'].content == '+33123456789'
    check_import_csv_journal(
        urls[0],
        [
            'import started',
            'user Elliott Thomas create',
            'user Elliott Thomas update property email : "elliot@universalpictures.com"',
            'user Elliott Thomas set email verified',
            'user Elliott Thomas update property first_name : "Elliott"',
            'user Elliott Thomas update property last_name : "Thomas"',
            'user Elliott Thomas update attribute more : "petit"',
            'user Elliott Thomas update attribute title : "Mr"',
            'user Elliott Thomas update attribute bike : True',
            'user Elliott Thomas update attribute saintsday : "2019-07-20"',
            'user Elliott Thomas update attribute birthdate : "1972-05-26"',
            'user Elliott Thomas update attribute zip : "75014"',
            'user Elliott Thomas update attribute phone : "+33123456789"',
            'import ended',
        ],
    )

    csv_lines[2] = 'et@universalpictures.com,ET,the Extra-Terrestrial,,,,,,42000,+3281123456'
    Event.objects.all().delete()
    response = import_csv('\n'.join(csv_lines), app)
    assert '0 rows have errors' in response.text
    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    check_import_csv_journal(
        urls[0],
        [
            'import started',
            'user ET the Extra-Terrestrial create',
            'user ET the Extra-Terrestrial update property email : "et@universalpictures.com"',
            'user ET the Extra-Terrestrial set email verified',
            'user ET the Extra-Terrestrial update property first_name : "ET"',
            'user ET the Extra-Terrestrial update property last_name : "the Extra-Terrestrial"',
            'user ET the Extra-Terrestrial update attribute more : ""',
            'user ET the Extra-Terrestrial update attribute title : ""',
            'user ET the Extra-Terrestrial update attribute bike : False',
            'user ET the Extra-Terrestrial update attribute zip : "42000"',
            'user ET the Extra-Terrestrial update attribute phone : "+3281123456"',
            'import ended',
        ],
    )

    assert User.objects.count() == user_count + 2
    et = User.objects.filter(email='et@universalpictures.com')[0]
    assert et.attributes.values['more'].content == ''
    assert et.attributes.values['title'].content == ''
    assert et.attributes.values['bike'].content == '0'
    assert 'saintsday' not in et.attributes.values
    assert 'birthdate' not in et.attributes.values
    assert et.attributes.values['zip'].content == '42000'
    assert et.attributes.values['phone'].content == '+3281123456'

    Event.objects.all().delete()
    # empty not mandatory phone
    csv_lines[2] = 'et@universalpictures.com,ET,the Extra-Terrestrial,,,,,,42000,'
    response = import_csv('\n'.join(csv_lines), app)
    et = User.objects.filter(email='et@universalpictures.com')[0]
    assert et.attributes.values['phone'].content == ''
    assert 'Enter a valid phone number' not in response.text
    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    check_import_csv_journal(
        urls[0],
        ['import started', 'user ET the Extra-Terrestrial update attribute phone : ""', 'import ended'],
    )


@unittest.mock.patch('authentic2.csv_import.UserCsvImporter.run', side_effect=Exception('boom'))
def test_user_import_exception(transactional_db, app, admin):
    Event.objects.all().delete()
    login(app, admin, '/manage/users/')
    response = import_csv('field\nvalue', app)
    assert '0 rows have errors' in response.text
    assert Event.objects.count() == 2
    evt = Event.objects.last()
    assert evt.message.endswith('import error')


def test_detail_view(app, admin, simple_user, freezer, user_ou1, ou1, settings):
    url = f'/manage/users/{simple_user.pk}/'
    resp = login(app, admin, url)
    assert str(simple_user.uuid) in resp.text
    assert 'Last activity' not in resp.text
    assert not resp.pyquery('.a2-manager-user-last-activity')
    simple_user.keepalive = datetime.datetime(2023, 2, 1, 7)
    simple_user.save()
    resp = app.get(url)
    assert 'Last activity on Feb. 1, 2023' in resp.pyquery('.a2-manager-user-last-activity')[0].text
    logout(app)

    ou1.clean_unused_accounts_alert = 700
    ou1.clean_unused_accounts_deletion = 730
    ou1.save()
    user_ou1.date_joined = user_ou1.last_login = datetime.datetime(2023, 1, 1, 3)
    user_ou1.save()

    issuer1, _ = Issuer.objects.get_or_create(entity_id='https://idp1.com/')
    UserSAMLIdentifier.objects.create(user=user_ou1, issuer=issuer1, name_id='1234')

    url = f'/manage/users/{user_ou1.pk}/'

    freezer.move_to('2023-01-01')
    resp = login(app, admin, url)
    assert not resp.pyquery('.a2-manager-user-date-alert')
    assert not resp.pyquery('.a2-manager-user-date-deletion')

    user_ou1.saml_identifiers.all().delete()
    resp = app.get(url)
    assert (
        'Deletion alert email planned for Dec. 1, 2024.'
        in resp.pyquery('.a2-manager-user-date-alert')[0].text
    )
    assert (
        'Account deletion planned for Dec. 31, 2024.'
        in resp.pyquery('.a2-manager-user-date-deletion')[0].text
    )
    logout(app)

    freezer.move_to('2024-12-10')
    resp = login(app, admin, url)
    assert (
        'Deletion alert email pending (should have been sent on Dec. 1, 2024).'
        in resp.pyquery('.a2-manager-user-date-alert')[0].text
    )
    user_ou1.last_account_deletion_alert = datetime.datetime(2024, 12, 1, 3)
    user_ou1.save()
    resp = app.get(url)
    assert (
        'Deletion alert email sent on Dec. 1, 2024, 3 a.m.'
        in resp.pyquery('.a2-manager-user-date-alert')[0].text
    )
    assert (
        'Account deletion planned for Dec. 31, 2024.'
        in resp.pyquery('.a2-manager-user-date-deletion')[0].text
    )
    logout(app)

    freezer.move_to('2025-01-01')
    resp = login(app, admin, url)
    assert (
        'Deletion alert email sent on Dec. 1, 2024, 3 a.m.'
        in resp.pyquery('.a2-manager-user-date-alert')[0].text
    )
    assert (
        'Account deletion pending (should have been performed on Dec. 31, 2024).'
        in resp.pyquery('.a2-manager-user-date-deletion')[0].text
    )

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': ['https://ldap.example.com/'],
            'basedn': 'o=ôrga',
            'realm': 'ldap1',
        }
    ]

    uid = UserExternalId.objects.create(user=user_ou1, source='ldap1', external_id='1234')
    resp = app.get(url)
    assert not resp.pyquery('.a2-manager-user-date-alert')
    assert not resp.pyquery('.a2-manager-user-date-deletion')
    uid.delete()
    settings.LDAP_AUTH_SETTINGS = []

    ou1.clean_unused_accounts_alert = ou1.clean_unused_accounts_deletion = None
    ou1.save()
    resp = app.get(url)
    assert not resp.pyquery('.a2-manager-user-date-alert')
    assert not resp.pyquery('.a2-manager-user-date-deletion')


def test_detail_view_user_deleted(app, admin, simple_user):
    url = f'/manage/users/{simple_user.pk}/'
    login(app, admin, url)
    simple_user.delete()
    app.get(url, status=404)


def test_user_table_user_deleted(app, admin, user_ou1, ou1):
    response = login(app, admin, '/manage/users/')
    assert len(response.pyquery('table.main tbody tr')) == 2

    user_ou1.delete()
    response = app.get('/manage/users/')
    assert len(response.pyquery('table.main tbody tr')) == 1


def test_user_import_row_error_display(transactional_db, app, admin):
    User.objects.create(first_name='Elliott', last_name='1', ou=get_default_ou())
    User.objects.create(first_name='Elliott', last_name='2', ou=get_default_ou())
    content = '''first_name key,last_name
Elliott,3'''
    login(app, admin, '/manage/users/')
    Event.objects.all().delete()
    response = import_csv(content, app)

    assert len(response.pyquery('table.main tbody tr.row-invalid')) == 2
    assert len(response.pyquery('table.main tbody tr.row-errors')) == 1
    assert 'matches too many user' in response.pyquery('tr.row-errors').text()

    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    check_import_csv_journal(urls[0], ['import started', 'import ended'])


def test_user_import_missing_roles_recap(transactional_db, app, admin):
    content = '''first_name key,last_name,_role_name
Elliott,Doe,test1
Jane,Doe,test1
John,Doe,test2'''
    login(app, admin, '/manage/users/')
    Event.objects.all().delete()
    response = import_csv(content, app)

    assert 'The following roles were missing: test1, test2' in response.text

    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    check_import_csv_journal(
        urls[0],
        [
            'import started',
            'user Elliott Doe create',
            'user Elliott Doe update property first_name : "Elliott"',
            'user Elliott Doe update property last_name : "Doe"',
            'user Jane Doe create',
            'user Jane Doe update property first_name : "Jane"',
            'user Jane Doe update property last_name : "Doe"',
            'user John Doe create',
            'user John Doe update property first_name : "John"',
            'user John Doe update property last_name : "Doe"',
            'import ended',
        ],
    )


def test_manager_create_user_next(superuser_or_admin, app, ou1):
    login(app, superuser_or_admin, '/manage/')

    next_url = '/example.nowhere.null/'
    url = '/manage/users/%s/add/?next=%s' % (ou1.pk, next_url)
    response = app.get(url)

    # cancel is not handled through form submission, it's a link
    # next without cancel, no cancel button
    assert response.pyquery.remove_namespaces()('a.cancel').attr('href') == '../..'
    assert response.pyquery.remove_namespaces()('input[name="next"]').attr('value') == next_url

    next_url = '/example.nowhere.null/$UUID/'
    cancel_url = '/example.nowhere.cancel/'
    url = '/manage/users/%s/add/?next=%s&cancel=%s' % (ou1.pk, next_url, cancel_url)
    response = app.get(url)

    assert response.pyquery.remove_namespaces()('a.cancel').attr('href') == cancel_url
    assert response.pyquery.remove_namespaces()('input[name="next"]').attr('value') == next_url

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    response = form.submit()
    user = User.objects.latest('id')
    assert urlparse(response.location).path == next_url.replace('$UUID', str(user.uuid))


def test_user_import_execute_from_simulation(transactional_db, app, admin):
    csv_content = '''first_name key,last_name
Elliott,3'''
    response = login(app, admin, '/manage/users/')
    Event.objects.all().delete()

    response = response.click('Import users')
    index = [i for i in response.forms if 'import_file' in response.forms[i].fields][0]
    response.forms[index].set(
        'import_file', Upload('users.csv', csv_content.encode('utf-8'), 'application/octet-stream')
    )
    response.forms[index].set('encoding', 'utf-8-sig')
    response.forms[index].set('ou', str(get_default_ou().pk))
    response = response.forms[index].submit().follow()
    response = response.forms['action-form'].submit(name='simulate').follow()

    start = time.time()
    response = response.click('Users Import')
    while 'Running' in response.text:
        response = response.click('Users Import')
        assert time.time() - start < 3
        time.sleep(0.1)

    urls = re.findall('<a href="(/manage/users/import/[^/]+/[^/]+/)">', response.text)
    response = app.get(urls[0])

    assert not User.objects.filter(first_name='Elliott').exists()
    # nothing in journal when simulating
    assert not list(Event.objects.order_by('timestamp', 'id'))

    response = response.form.submit(name='execute').follow()
    while 'Running' in response.text:
        response = response.click('Users Import')
        assert time.time() - start < 3
        time.sleep(0.1)

    assert User.objects.filter(first_name='Elliott').exists()
    assert list(Event.objects.order_by('timestamp', 'id'))


def test_manager_create_user_next_form_error(superuser_or_admin, app, ou1):
    next_url = '/example.nowhere.null/'
    url = '/manage/users/%s/add/?next=%s' % (ou1.pk, next_url)
    login(app, superuser_or_admin, '/manage/')
    response = app.get(url)
    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'jd')  # erroneous
    form.set('password1', 'notvalid')  # erroneous
    assert '<input type="hidden" name="next" value="%s">' % next_url in form.submit().text


def test_manager_add_user_querystring(superuser_or_admin, app, ou1):
    querystring = 'stay_here=true'
    url = '/manage/users/add/?%s' % querystring
    login(app, superuser_or_admin, '/manage/')
    response = app.get(url)

    assert querystring in response.location


def test_manager_edit_user_next(app, simple_user, superuser_or_admin):
    next_url = '/example.nowhere.null/'
    url = '/manage/users/%s/edit/?next=%s' % (simple_user.pk, next_url)
    login(app, superuser_or_admin, '/manage/')
    response = app.get(url)

    # cancel if not handled through form submission
    assert response.pyquery.remove_namespaces()('a.cancel').attr('href') == next_url

    form = response.form
    form.set('last_name', 'New name')
    assert urlparse(form.submit().location).path == next_url


def test_manager_edit_user_next_form_error(superuser_or_admin, app, ou1, simple_user):
    next_url = '/example.nowhere.null/'
    url = '/manage/users/%s/edit/?next=%s' % (simple_user.pk, next_url)
    login(app, superuser_or_admin, '/manage/')
    response = app.get(url)
    form = response.form
    form.set('email', 'jd')  # erroneous
    resp = form.submit()
    assert '<input type="hidden" name="next" value="%s">' % next_url in resp.ubody


def test_user_add_settings(settings, admin, app, db):
    passwd_options = ('generate_password', 'reset_password_at_next_login', 'send_mail', 'send_password_reset')
    for policy in [choice[0] for choice in OU.USER_ADD_PASSWD_POLICY_CHOICES]:
        ou = get_default_ou()
        ou.user_add_password_policy = policy
        ou.save()
        user_add = login(app, admin, '/manage/users/add/').follow()
        for option, i in zip(passwd_options, range(4)):
            assert user_add.form.get(option).value == {False: None, True: 'on'}.get(
                OU.USER_ADD_PASSWD_POLICY_VALUES[policy][i]
            )
        app.get('/logout/').form.submit()


def test_ou_hide_username(admin, app, db):
    some_ou = OU.objects.create(name='Some Ou', show_username=False)

    login(app, admin, '/manage/')
    url = '/manage/users/%s/add/' % some_ou.pk
    response = app.get(url)
    q = response.pyquery.remove_namespaces()
    assert len(q('p[id="id_username_p"]')) == 0

    form = response.form
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('email', 'john.doe@gmail.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    form.submit()

    assert User.objects.get(email='john.doe@gmail.com')


def test_manager_edit_user_email_verified(app, simple_user, superuser_or_admin):
    simple_user.set_email_verified(True)
    simple_user.save()

    url = '/manage/users/%s/edit/' % simple_user.pk
    login(app, superuser_or_admin, '/manage/')

    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    old_password = user.password

    response = app.get(url)
    form = response.form
    form.set('email', 'new.email@gmail.net')
    response = form.submit().follow()

    user = User.objects.get(id=simple_user.id)
    assert not user.email_verified
    assert old_password == user.password


def test_manager_edit_user_address_autocomplete(app, simple_user, superuser_or_admin):
    url = '/manage/users/%s/edit/' % simple_user.pk
    login(app, superuser_or_admin, '/manage/')

    Attribute.objects.create(
        name='address_autocomplete',
        label='Address (autocomplete)',
        kind='address_auto',
        user_visible=True,
        user_editable=True,
    )

    resp = app.get(url)
    assert resp.html.find('select', {'name': 'address_autocomplete'})
    assert resp.html.find('input', {'id': 'manual-address'})


def test_manager_email_verified_column_user(app, simple_user, superuser_or_admin):
    login(app, superuser_or_admin, '/manage/')

    resp = app.get('/manage/users/')
    assert not resp.html.find('span', {'class': 'verified'})

    simple_user.set_email_verified(True)
    simple_user.save()
    resp = app.get('/manage/users/')
    assert resp.html.find('span', {'class': 'verified'}).text == simple_user.email


def test_manager_user_link_column_is_active(app, simple_user, superuser_or_admin):
    login(app, superuser_or_admin, '/manage/')

    resp = app.get('/manage/users/')
    assert not resp.html.find('span', {'class': 'disabled'})

    simple_user.is_active = False
    simple_user.save()
    resp = app.get('/manage/users/')
    assert resp.html.find('span', {'class': 'disabled'}).text == 'Jôhn Dôe (disabled)'


def test_manager_user_disabled_user(app, superuser, simple_user):
    login(app, superuser, '/manage/')

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert resp.pyquery('h2').text() == 'Jôhn Dôe'

    simple_user.is_active = False
    simple_user.save()
    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert resp.pyquery('h2').text() == 'Jôhn Dôe disabled'
    assert resp.pyquery('h2 .disabled-badge')


def test_manager_user_edit_reset_phone(app, superuser, simple_user, phone_activated_authn):
    simple_user.attributes.phone = '+33122334455'
    simple_user.phone_verified_on = now()
    simple_user.save()

    login(app, superuser, '/manage/')
    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))

    resp.form.set('phone_1', '111221122')
    resp.form.submit()
    simple_user.refresh_from_db()
    assert simple_user.phone_identifier == '+33111221122'
    assert not simple_user.phone_verified_on

    simple_user.phone_verified_on = now()
    simple_user.save()

    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))
    resp.form.set('phone_1', '')
    resp.form.submit()
    simple_user.refresh_from_db()
    assert not simple_user.phone_identifier
    assert not simple_user.phone_verified_on


def test_manager_user_username_field(app, superuser, simple_user):
    login(app, superuser, '/manage/')

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})
    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})

    # remove username from user
    simple_user.username = ''
    simple_user.save()

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})
    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})

    # disable usernames on organizational unit
    simple_user.ou.show_username = False
    simple_user.ou.save()

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert not resp.html.find('input', {'name': 'username'})
    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))
    assert not resp.html.find('input', {'name': 'username'})

    # but it's still displayed if it was set
    simple_user.username = 'user'
    simple_user.save()

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})
    resp = app.get(reverse('a2-manager-user-edit', kwargs={'pk': simple_user.id}))
    assert resp.html.find('input', {'name': 'username'})


def test_manager_user_address_autocomplete_field(app, superuser, simple_user):
    login(app, superuser, '/manage/')
    Attribute.objects.create(
        name='address_autocomplete',
        label='Address (autocomplete)',
        kind='address_auto',
        user_visible=True,
        user_editable=True,
    )
    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert not resp.html.find('select', {'name': 'address_autocomplete'})
    assert not resp.html.find('input', {'id': 'manual-address'})


def test_manager_user_roles_visibility(app, simple_user, admin, ou1, ou2):
    role1 = Role.objects.create(name='Role 1', slug='role1', ou=ou1)
    role2 = Role.objects.create(name='Role 2', slug='role2', ou=ou2)
    simple_user.roles.add(role1)
    simple_user.roles.add(role2)
    simple_user.save()

    login(app, admin, '/manage/')

    resp = app.get(reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}))
    assert '/manage/roles/%s/' % role1.pk in resp.text
    assert 'Role 1' in resp.text
    assert '/manage/roles/%s/' % role2.pk in resp.text
    assert 'Role 2' in resp.text

    app.get('/logout/').form.submit()

    other_user = get_user_model().objects.create(username='other_user', ou=ou1)
    other_user.set_password('auietsrn')
    other_role = Role.objects.create(name='Other role', slug='other-role', ou=ou1)
    view_role1_perm = Permission.objects.create(
        operation=get_operation(VIEW_OP),
        target_ct=ContentType.objects.get_for_model(Role),
        target_id=role1.pk,
    )
    other_role.permissions.add(get_search_user_perm())
    other_role.permissions.add(view_role1_perm)
    other_role.save()
    other_user.roles.add(other_role)
    other_user.save()

    login(app, other_user, '/manage/', password='auietsrn')
    app.get(
        reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id}), status=403
    )  # search_user in not enough to view users -> NOK
    app.get('/manage/roles/%s/' % role1.pk)  # OK
    app.get('/manage/roles/%s/' % role2.pk, status=403)  # NOK


def test_manager_user_roles_search(app, simple_user, admin):
    role1 = Role.objects.create(name='Role 1', slug='role1', ou=get_default_ou())
    role2 = Role.objects.create(name='Another name', slug='another', ou=get_default_ou())
    simple_user.roles.add(role1)
    simple_user.roles.add(role2)
    simple_user.save()

    resp = login(app, admin, '/manage/users/%s/roles/' % simple_user.pk)
    form = resp.forms[0]
    # form.set('search-text', 'role')
    resp = form.submit()
    assert resp.pyquery('tr[data-pk="%s"]' % role1.pk)
    assert resp.pyquery('tr[data-pk="%s"]' % role2.pk)

    form = resp.forms[0]
    form.set('search-text', 'role')
    resp = form.submit()
    assert resp.pyquery('tr[data-pk="%s"]' % role1.pk)
    assert not resp.pyquery('tr[data-pk="%s"]' % role2.pk)

    form = resp.forms[0]
    form.set('search-text', 'other')
    resp = form.submit()
    assert not resp.pyquery('tr[data-pk="%s"]' % role1.pk)
    assert resp.pyquery('tr[data-pk="%s"]' % role2.pk)


def test_manager_user_authorizations(app, superuser, simple_user):
    """
    for 3 kind of users:
    * check if a button is provided on user detail page
    * access user service consents page
    * try to remove a service consent
    """
    from authentic2.a2_rbac.models import MANAGE_AUTHORIZATIONS_OP
    from tests.conftest import create_user

    user_detail_url = reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id})
    user_authorizations_url = reverse('a2-manager-user-authorizations', kwargs={'pk': simple_user.id})

    resp = login(app, superuser)
    resp = app.get(user_detail_url, status=200)
    assert user_authorizations_url not in [
        x['href'] for x in resp.html.find('ul', {'class': 'extra-actions-menu'}).find_all('a')
    ]

    # add a service consent to simple_user
    oidc_client = OIDCClient.objects.create(
        name='client', slug='client', ou=simple_user.ou, redirect_uris='https://example.com/'
    )

    resp = app.get(user_detail_url, status=200)
    assert user_authorizations_url in [
        x['href'] for x in resp.html.find('ul', {'class': 'extra-actions-menu'}).find_all('a')
    ]

    auth = OIDCAuthorization.objects.create(
        client=oidc_client, user=simple_user, scopes='openid', expired='2020-01-01T12:01:01Z'
    )
    assert OIDCAuthorization.objects.count() == 1

    view_user_perm = Permission.objects.create(
        operation=get_operation(VIEW_OP),
        target_ct=ContentType.objects.get_for_model(User),
        target_id=simple_user.pk,
    )
    view_user_role = Role.objects.create(name='view_user', ou=simple_user.ou)
    view_user_role.permissions.add(view_user_perm)

    manage_auth_perm = Permission.objects.create(
        operation=get_operation(MANAGE_AUTHORIZATIONS_OP),
        target_ct=ContentType.objects.get_for_model(User),
        target_id=simple_user.pk,
    )
    manage_auth_role = Role.objects.create(name='manage_auth', ou=simple_user.ou)
    manage_auth_role.permissions.add(manage_auth_perm)

    user1 = create_user(username='agent1', ou=simple_user.ou)
    user2 = create_user(username='agent2', ou=simple_user.ou)
    user2.roles.add(view_user_role)
    user3 = create_user(username='agent3', ou=simple_user.ou)
    user3.roles.add(manage_auth_role)

    # user1 without permission
    resp = login(app, user1)
    resp = app.get(user_detail_url, status=403)
    assert 'You are not authorized to see this page' in resp.text
    resp = app.get(user_authorizations_url, status=403)
    assert 'You are not authorized to see this page' in resp.text
    params = {'authorization': auth.pk, 'csrfmiddlewaretoken': '???'}
    resp = app.post(user_authorizations_url, params=params, status=302)
    assert OIDCAuthorization.objects.count() == 1

    # user2 can see auth authorizations
    resp = login(app, user2)
    resp = app.get(user_detail_url, status=200)
    assert user_authorizations_url in [
        x['href'] for x in resp.html.find('ul', {'class': 'extra-actions-menu'}).find_all('a')
    ]
    resp = resp.click('Consents')
    assert resp.html.find('h2').text == 'Consent Management'
    assert resp.html.find('td', {'class': 'remove-icon-column'}).a['class'] == ['disabled']
    # cannot click it's JS :/
    token = str(resp.context['csrf_token'])
    params = {'authorization': auth.pk, 'csrfmiddlewaretoken': token}
    resp = app.post(user_authorizations_url, params=params, status=302)
    assert OIDCAuthorization.objects.count() == 1

    # user3 can remove auth authorizations
    resp = login(app, user3)
    resp = app.get(user_detail_url, status=200)
    assert user_authorizations_url in [
        x['href'] for x in resp.html.find('ul', {'class': 'extra-actions-menu'}).find_all('a')
    ]
    resp = resp.click('Consents')
    resp = app.get(user_authorizations_url, status=200)
    assert resp.html.find('h2').text == 'Consent Management'
    assert resp.html.find('td', {'class': 'remove-icon-column'}).a['class'] == ['js-remove-object']
    # cannot click it's JS :/
    token = str(resp.context['csrf_token'])
    params = {'authorization': auth.pk, 'csrfmiddlewaretoken': token}
    resp = app.post(
        user_authorizations_url, params=params, status=302, headers={'Referer': 'https://testserver/'}
    )
    assert OIDCAuthorization.objects.count() == 0
    resp = resp.follow()
    assert resp.html.find('td').text == 'This user has not granted profile data access to any service yet.'


def test_manager_user_authorizations_breadcrumb(app, superuser, simple_user):
    resp = login(app, superuser)
    user_authorizations_url = reverse('a2-manager-user-authorizations', kwargs={'pk': simple_user.id})
    resp = app.get(user_authorizations_url, status=200)
    assert [x.text for x in resp.html.find('span', {'id': 'breadcrumb'}).find_all('a')] == [
        'Homepage',
        'Administration',
        'Users',
        'Default organizational unit',
        'Jôhn Dôe',
        'Consent Management',
    ]
    user_authorizations_url = reverse('a2-manager-user-authorizations', kwargs={'pk': superuser.id})
    resp = app.get(user_authorizations_url, status=200)
    assert [x.text for x in resp.html.find('span', {'id': 'breadcrumb'}).find_all('a')] == [
        'Homepage',
        'Administration',
        'Users',
        'super user',
        'Consent Management',
    ]


def test_manager_user_sidebar_template_value(app, superuser, simple_user, settings):
    resp = login(app, superuser)

    # users page contains popup link
    resp = app.get(reverse('a2-manager-users'))
    assert resp.pyquery('.extra-actions-menu a[rel="popup"]')[0].get('href') == reverse(
        'a2-manager-users-advanced-configuration'
    )

    user_detail_url = reverse('a2-manager-user-detail', kwargs={'pk': simple_user.id})
    setting = Setting.objects.get(key='users:backoffice_sidebar_template')

    # correct template
    setting.value = 'User {{ object }} may have temporary roles.'
    setting.save()

    resp = app.get(user_detail_url, status=200)
    assert 'User Jôhn Dôe may have temporary roles.' in resp.pyquery('#advanced-info')[0].text

    # condition correctly evaluated
    setting.value = '{% if not object %}Foo{% else %}Bar{% endif %}'
    setting.save()

    resp = app.get(user_detail_url, status=200)
    assert 'Bar' in resp.pyquery('#advanced-info')[0].text
    assert 'Foo' not in resp.pyquery('#advanced-info')[0].text

    # missing context key rendered to empty value, no error
    setting.value = 'User {{ foo }} may have temporary roles.'
    setting.save()

    resp = app.get(user_detail_url, status=200)
    assert 'User  may have temporary roles.' in resp.pyquery('#advanced-info')[0].text

    # erroneous template not rendered
    setting.value = 'User {{ user %} may have temporary roles.'
    setting.save()

    resp = app.get(user_detail_url, status=200)
    assert 'User {{ user %} may have temporary roles.' in resp.pyquery('#advanced-info')[0].text

    # html is also rendered
    setting.value = '<strong>User {{ object.email }} may have temporary roles.</strong>'
    setting.save()

    resp = app.get(user_detail_url, status=200)
    assert 'User user@example.net may have temporary roles.' in resp.pyquery('#advanced-info strong')[0].text

    # make sure the template vars are available in the context
    settings.TEMPLATE_VARS = {'portal_url': 'https://combo.publik.love/'}
    setting.value = '<strong>User {{ object.email }} from <a href="{{ portal_url }}">portal</a> may have temporary roles.</strong>'
    setting.save()
    resp = app.get(user_detail_url, status=200)
    assert resp.pyquery('#advanced-info strong a').attr('href') == 'https://combo.publik.love/'


def test_manager_user_roles_breadcrumb(app, superuser, simple_user):
    resp = login(app, superuser)
    user_roles_url = reverse('a2-manager-user-roles', kwargs={'pk': simple_user.id})
    resp = app.get(user_roles_url, status=200)
    assert [x.text for x in resp.html.find('span', {'id': 'breadcrumb'}).find_all('a')] == [
        'Homepage',
        'Administration',
        'Users',
        'Default organizational unit',
        'Jôhn Dôe',
        'Roles',
    ]
    user_roles_url = reverse('a2-manager-user-roles', kwargs={'pk': superuser.id})
    resp = app.get(user_roles_url, status=200)
    assert [x.text for x in resp.html.find('span', {'id': 'breadcrumb'}).find_all('a')] == [
        'Homepage',
        'Administration',
        'Users',
        'super user',
        'Roles',
    ]


def test_manager_create_user_duplicates(admin, app, ou1, settings):
    settings.A2_MANAGER_CHECK_DUPLICATE_USERS = True
    Attribute.objects.create(
        kind='birthdate', name='birthdate', label='birthdate', required=False, searchable=True
    )

    user = User.objects.create(
        first_name='Alexander', last_name='Longname', email='alexandre.longname@entrouvert.com'
    )
    user.attributes.birthdate = datetime.date(1980, 1, 2)
    user2 = User.objects.create(first_name='Alexandra', last_name='Longname')
    user3 = User.objects.create(first_name='Alex', last_name='Shortname')

    login(app, admin)
    resp = app.get('/manage/users/%s/add/' % ou1.pk)

    form = resp.form
    form.set('first_name', 'Alexandre')
    form.set('last_name', 'Longname')
    form.set('email', 'alex@entrouvert.com')
    form.set('password1', 'ABcd1234')
    form.set('password2', 'ABcd1234')
    resp = form.submit()

    assert 'user may already exist' in resp.text
    assert 'Alexander Longname' in resp.text
    assert '- alexandre.longname@entrouvert.com' in resp.text
    assert '- 1980-01-02' in resp.text
    assert '/users/%s/' % user.pk in resp.text
    assert 'Alexandra Longname' in resp.text
    assert '/users/%s/' % user2.pk in resp.text

    # This user was in fact duplicate. Agent reuses the form to fill details on another user
    form = resp.form
    form.set('first_name', 'Alexa')
    form.set('last_name', 'Shortname')
    form.set('email', 'ashortname@entrouvert.com')
    resp = form.submit()

    assert 'user may already exist' in resp.text
    assert '/users/%s/' % user3.pk in resp.text

    # Not a duplicate this time. Simply submitting again creates user
    resp = resp.form.submit().follow()
    assert User.objects.filter(first_name='Alexa').count() == 1


def test_delete_user(app, superuser, simple_user):
    Event.objects.all().delete()
    assert User.objects.count() == 2
    assert Event.objects.filter(user=simple_user, type__name='manager.user.deletion').count() == 0

    response = login(app, superuser, '/manage/users/')
    response = response.click('Jôhn Dôe')
    response = response.click('Delete')
    response = response.form.submit(value='Delete')

    assert User.objects.count() == 1
    assert (
        Event.objects.filter(user=superuser, type__name='manager.user.deletion')
        .which_references(simple_user)
        .count()
        == 1
    )
