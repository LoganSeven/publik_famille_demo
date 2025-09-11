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
import json

import pytest
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from pyquery import PyQuery
from webtest import Upload

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import User

from .utils import login, request_select2, text_content


def test_manager_role_export(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-roles')

    export_response = response.click('Export')
    export = export_response.json

    assert list(export.keys()) == ['roles']
    assert len(export['roles']) == 2
    assert {role['slug'] for role in export['roles']} == {'role_ou1', 'role_ou2'}

    export_response = response.click('CSV', href='/export/')
    reader = csv.reader(
        [force_str(line) for line in export_response.body.split(force_bytes('\r\n'))], delimiter=','
    )
    rows = [row for row in reader]

    assert rows[0] == ['name', 'slug', 'members', 'ou']
    assert len(rows) - 2 == 2  # csv header and last EOL
    assert {row[1] for row in rows[1:3]} == {'role_ou1', 'role_ou2'}
    assert {row[3] for row in rows[1:3]} == {'OU1', 'OU2'}

    response.form.set('search-text', 'role_ou1')
    search_response = response.form.submit()

    export_response = search_response.click('Export')
    export = export_response.json

    assert list(export.keys()) == ['roles']
    assert len(export['roles']) == 1
    assert export['roles'][0]['slug'] == 'role_ou1'

    export_response = search_response.click('CSV', href='/export/')
    reader = csv.reader(
        [force_str(line) for line in export_response.body.split(force_bytes('\r\n'))], delimiter=','
    )
    rows = [row for row in reader]

    assert rows[0] == ['name', 'slug', 'members', 'ou']
    assert len(rows) - 2 == 1  # csv header and last EOL
    assert rows[1][1] == 'role_ou1'


def test_manager_role_export_escape_formula(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-roles')
    role_ou1.name = '= 1 + 2'
    role_ou1.save()

    export_response = response.click('CSV', href='/export/')
    reader = csv.reader(
        [force_str(line) for line in export_response.body.split(force_bytes('\r\n'))], delimiter=','
    )
    cells = [cell for row in reader for cell in row]
    assert '= 1 + 2' not in cells
    assert '\'= 1 + 2' in cells


def test_manager_role_name_uniqueness_single_ou(app, admin):
    response = login(app, admin, 'a2-manager-roles')

    response = response.click('Add')
    response.form.set('name', 'Role1')
    response = response.form.submit('Save').follow()
    response = response.click('Roles')
    assert response.pyquery('td.name').text() == 'Role1'

    response = response.click('Add')
    response.form.set('name', 'Role1')
    response = response.form.submit('Save')
    assert response.pyquery('.error').text() == 'Name already used'


def test_manager_role_name_uniqueness_multiple_ou(app, admin, ou1):
    response = login(app, admin, 'a2-manager-roles')

    response = response.click('Add')
    response.form.set('ou', str(ou1.id))
    response.form.set('name', 'Role1')
    response = response.form.submit('Save').follow()
    response = response.click('Roles')
    assert response.pyquery('td.name').text() == 'Role1'

    response = response.click('Add')
    response.form.set('ou', str(ou1.id))
    response.form.set('name', 'Role1')
    response = response.form.submit('Save')
    assert response.pyquery('.error').text() == 'Name already used'


def test_role_members_via(app, admin):
    user1 = User.objects.create(username='user1')
    user2 = User.objects.create(username='user2')
    role1 = Role.objects.create(name='role1')
    role2 = Role.objects.create(name='role2')

    role1.add_child(role2)
    user1.roles.add(role1)
    user2.roles.add(role2)

    response = login(app, admin, '/manage/roles/%s/' % role1.id)
    response.forms['search-form']['search-all_members'] = True
    response = response.forms['search-form'].submit()
    rows = list(
        zip(
            [text_content(el) for el in response.pyquery('tr td.username')],
            [text_content(el) for el in response.pyquery('tr td.direct')],
            [text_content(el) for el in response.pyquery('tr td.via')],
        )
    )
    assert rows == [
        ('user1', '✔', ''),
        ('user2', '✘', 'role2'),
    ]


def test_role_members_with_uuid(app, admin):
    role = Role.objects.create(name='Foo role')

    response = login(app, admin, '/manage/roles/uuid:%s/' % role.uuid)
    assert 'Foo role' in str(response.body)


def test_manager_role_import(app, admin, ou1, role_ou1, ou2, role_ou2):
    response = login(app, admin, 'a2-manager-roles')

    export_response = response.click('Export')
    export = export_response.json

    assert len(export['roles']) == 2
    assert not 'ous' in export
    Role.objects.filter(ou__in=[ou1, ou2]).delete()

    # import in OUs specified in export file
    resp = app.get('/manage/roles/')
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()

    assert Role.objects.filter(name=role_ou1.name, ou=ou1).exists()
    assert Role.objects.filter(name=role_ou2.name, ou=ou2).exists()
    Role.objects.filter(ou__in=[ou1, ou2]).delete()

    # import in custom OU
    resp = app.get('/manage/roles/')
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp.form['ou'] = get_default_ou().pk
    resp = resp.form.submit().follow()

    assert Role.objects.filter(name=role_ou1.name, ou=get_default_ou()).exists()
    assert Role.objects.filter(name=role_ou2.name, ou=get_default_ou()).exists()

    response.form.set('search-text', 'role_ou1')
    response.form.submit()

    export_response = response.click('Export')
    new_export = export_response.json
    assert len(export['roles']) == 2
    uuids = {role['uuid'] for role in export['roles']}
    new_uuids = {role['uuid'] for role in new_export['roles']}
    assert uuids == new_uuids

    # import in custom OU while roles exist in another OU
    resp = app.get('/manage/roles/')
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp.form['ou'] = ou1.pk
    resp = resp.form.submit().follow()

    assert Role.objects.filter(name=role_ou1.name, ou=get_default_ou()).exists()
    assert Role.objects.filter(name=role_ou2.name, ou=get_default_ou()).exists()
    assert Role.objects.filter(ou=ou1).count() == 4

    # in case ous are present in export file, they must not be imported
    export['ous'] = [
        {
            'uuid': '27255f404cb140df9a577da76b59f285',
            'slug': 'should_not_exist',
            'name': 'should_not_exist',
        }
    ]
    resp = app.get('/manage/roles/')  # unselect ou1
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp.form['ou'] = get_default_ou().pk
    resp = resp.form.submit().follow()

    assert not OrganizationalUnit.objects.filter(slug='should_not_exist').exists()

    # missing ou in export file
    export = {'roles': [{'slug': 'agent', 'name': 'Agent'}]}
    resp = app.get('/manage/roles/')
    resp = resp.click('Import')
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()
    assert 'Missing Organizational Unit for role: Agent' in resp.text

    resp.form['ou'] = get_default_ou().pk
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit().follow()
    assert Role.objects.get(slug='agent')


def test_manager_role_import_selected_ou(app, admin, ou1, ou2):
    response = login(app, admin, 'a2-manager-roles')
    response.form.set('search-ou', ou2.pk)
    response = response.form.submit()
    response = response.click('Import')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'OU2'


def test_manager_role_import_ou_permission(app, admin, ou1, role_ou1, ou2, role_ou2, admin_ou1):
    resp = login(app, admin, 'a2-manager-roles')
    resp = resp.click('Export')
    export = resp.json
    assert len(export['roles']) == 2
    role_ou1.delete()
    role_ou2.delete()
    app.session.flush()  # logout

    resp = login(app, admin_ou1, 'a2-manager-roles')
    resp = resp.click('Import')
    resp.form['ou'] = ''
    resp.form['site_json'] = Upload('export.json', json.dumps(export).encode(), 'application/json')
    resp = resp.form.submit()

    # importing fails because user has no permission on ou2
    assert 'missing permissions on Organizational Unit' in resp.text


def test_manager_role_add_selected_ou(app, admin, ou1, ou2):
    response = login(app, admin, '/manage/roles/')
    response = response.click('Add role')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    response = app.get('/manage/roles/')
    response.form.set('search-ou', ou2.pk)
    response = response.form.submit()
    response = response.click('Add role')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'OU2'


def test_roles_displayed_fields(app, admin, ou1, ou2):
    login(app, admin)
    role1 = Role.objects.create(name='role1')
    role2 = Role.objects.create(name='role2')

    user1 = User.objects.create(username='user1')
    user2 = User.objects.create(username='user2')
    user1.roles.add(role1)
    user2.roles.add(role1)
    role2.add_child(role1)  # indirect members

    assert role1.can_manage_members
    role2.can_manage_members = False  # user syncronized from LDAP
    role2.save()

    # check OUTable
    response = app.get('/manage/roles/')
    rows = set(
        zip(
            [text_content(el) for el in response.pyquery('tr td.name')],
            [text_content(el) for el in response.pyquery('tr td.member_count')],
        )
    )
    assert rows == {
        ('role1', '2'),
        ('role2 (LDAP)', '0'),
    }

    # check UserRolesTable
    response = app.get('/manage/users/%s/roles/?search-ou=all' % user2.pk)
    rows = set(
        zip(
            [text_content(el) for el in response.pyquery('tr td.name')],
            [text_content(el) for el in response.pyquery('tr td.via')],
        )
    )
    assert rows == {
        ('role1', ''),
        ('role2 (LDAP)', 'role1 '),
    }

    # check OuUserRolesTable
    response = app.get('/manage/users/%s/roles/?search-ou=' % user2.pk)
    rows = set(
        zip(
            [text_content(el) for el in response.pyquery('tr td.name')],
            [text_content(el) for el in response.pyquery('tr td.via')],
            [el.attrib.get('checked') for el in response.pyquery('tr td.member input')],
            [el.attrib.get('disabled') for el in response.pyquery('tr td.member input')],
        )
    )
    assert rows == {
        ('role1', '', 'checked', None),
        ('role2 (LDAP)', 'role1 ', None, 'disabled'),
    }


def test_manager_role_csv_import(app, admin, ou1, ou2):
    roles_count = Role.objects.count()
    resp = login(app, admin, '/manage/roles/')

    resp = resp.click('CSV import')
    csv_header = b'name,slug,ou\n'
    csv_content = 'Role Name,role_slug,%s' % ou1.slug
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp.form.submit(status=302)
    assert Role.objects.get(name='Role Name', slug='role_slug', ou=ou1)
    assert Role.objects.count() == roles_count + 1

    csv_content = 'Role 2,role2,\nRole 3,,%s' % ou2.slug
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp.form.submit(status=302)
    assert Role.objects.get(name='Role 2', slug='role2', ou=get_default_ou())
    assert Role.objects.get(name='Role 3', slug='role-3', ou=ou2)
    assert Role.objects.count() == roles_count + 3

    # slug can be updated using name, name can be updated using slug
    csv_content = 'Role two,role2,\nRole 3,role-three,%s' % ou2.slug
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp.form.submit(status=302)
    assert Role.objects.get(name='Role two', slug='role2', ou=get_default_ou())
    assert Role.objects.get(name='Role 3', slug='role-three', ou=ou2)
    assert Role.objects.count() == roles_count + 3

    # conflict in auto-generated slug is handled
    csv_header = b'name\n'
    csv_content = 'Role!2'
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp.form.submit(status=302)
    assert Role.objects.get(name='Role!2', slug='role2-1', ou=get_default_ou())
    assert Role.objects.count() == roles_count + 4

    # Identical roles are created only once
    csv_content = 'Role 4,role-4,\nRole 4,,\nRole 4,role-4,'
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp.form.submit(status=302)
    assert Role.objects.get(name='Role 4', slug='role-4', ou=get_default_ou())
    assert Role.objects.count() == roles_count + 5

    csv_content = 'xx\0xx,,'
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp = resp.form.submit()
    assert 'Invalid file format.' in resp.text

    wrong_header = b'a,b,c\n'
    resp.form['import_file'] = Upload('t.csv', wrong_header, 'text/csv')
    resp = resp.form.submit()
    assert 'Invalid file header' in resp.text

    csv_content = ',slug-but-no-name,\nRole,,unknown-ou'
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp = resp.form.submit()
    assert 'Name is required. (line 2)' in resp.text
    assert 'Organizational Unit unknown-ou does not exist. (line 3)' in resp.text

    csv_content = 'Role 5,invalid slug,%s\nRole 6,,' % ou1.slug
    resp.form['import_file'] = Upload('t.csv', csv_header + csv_content.encode(), 'text/csv')
    resp = resp.form.submit()
    assert 'Invalid slug "invalid slug". (line 2)' in resp.pyquery('.error').text()

    resp = app.get('/manage/roles/csv-import/')
    resp = resp.click('Download sample')
    assert 'name,slug,ou' in resp.text


@pytest.mark.parametrize(
    'role_names,search_text,expt_found',
    [
        (
            ['A random test rôle', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            ' rand role',
            [0],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            ' test  rand  ',
            [0, 1],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            'test   some',
            [2, 4],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            '  SoMe   ',
            [2, 3, 4],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            ' teste some',
            [],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            '  ',
            [0, 1, 2, 3, 4],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Something else', 'SomeTest'],
            'rôle',
            [0, 2],
        ),
        (
            ['A random test role', 'Random test stuff', 'Some test role', 'Some test', 'SomeTest'],
            'some-test',
            [2, 3],
        ),
    ],
)
def test_manager_role_search(app, admin, role_names, search_text, expt_found):
    roles = [Role.objects.create(name=name, ou=get_default_ou()) for name in role_names]
    resp = login(app, admin, '/manage/roles/')
    resp.form['search-text'] = search_text

    result = resp.form.submit()
    for expt_role in [roles[i] for i in expt_found]:
        assert (
            len(PyQuery(result.text).find(f'tr[data-pk="{expt_role.pk}"]')) == 1
        ), 'Role %r should be found with %r' % (expt_role.name, search_text)

    for non_expt_role_pk in [roles[i].pk for i in range(len(role_names)) if i not in expt_found]:
        assert (
            len(PyQuery(result.text).find(f'tr[data-pk="{non_expt_role_pk}"]')) == 0
        ), 'Role %r should not be found with %r' % (expt_role.name, search_text)


def test_role_members_display_role_parents(app, superuser, settings, simple_role):
    url = reverse('a2-manager-role-members', kwargs={'pk': simple_role.pk})

    resp = login(app, superuser, url)
    assert "This role doesn't contain permissions of any other role." in resp.text

    for i in range(10):
        role = Role.objects.create(name=f'Role {i}', ou=get_default_ou())
        simple_role.add_parent(role)

    resp = app.get(url)
    assert "This role doesn't contain permissions of any other role." not in resp.text

    for i, el in enumerate(resp.pyquery.find('a.role-inheritance-parent')):
        assert el.text == f'Role {i}'
    assert '(view all roles)' not in resp.text

    role = Role.objects.create(name='Role a', ou=get_default_ou())
    simple_role.add_parent(role)
    resp = app.get(url)
    assert 'Role a' not in resp.text
    assert '(view all roles)' in resp.text

    resp = resp.click('(view all roles)')
    assert 'Role a' in resp.text

    # display OU if there are more than one
    ou1 = OrganizationalUnit.objects.create(name='ou1')
    resp = app.get(url)
    for i, el in enumerate(resp.pyquery.find('a.role-inheritance-parent')):
        assert el.text == f'Default organizational unit - Role {i}'

    # sort by OU, then name
    Role.objects.filter(name__in=['Role 2', 'Role 3', 'Role 4']).update(ou=ou1)
    Role.objects.filter(name__in=['Role 5', 'Role 6']).update(ou=None)

    resp = app.get(url)
    assert [el.text for el in resp.pyquery.find('a.role-inheritance-parent')] == [
        'Role 5',
        'Role 6',
        'Default organizational unit - Role 0',
        'Default organizational unit - Role 1',
        'Default organizational unit - Role 7',
        'Default organizational unit - Role 8',
        'Default organizational unit - Role 9',
        'Default organizational unit - Role a',
        'ou1 - Role 2',
        'ou1 - Role 3',
    ]


def test_role_members_display_role_parents_search(app, superuser, simple_role):
    Role.objects.create(name='Role 1', ou=get_default_ou())

    url = reverse('a2-manager-role-members', kwargs={'pk': simple_role.pk})
    resp = login(app, superuser, url)

    resp = resp.click('Edit', href='parents')
    assert [el.text_content() for el in resp.pyquery.find('tbody td.name')] == ['Role 1']

    resp.form['search-internals'] = True
    resp = resp.form.submit()
    roles = [el.text_content() for el in resp.pyquery.find('tbody td.name')]
    assert 'Role 1' in roles
    assert 'Manager' in roles
    assert 'Managers of role "simple role"' not in roles

    resp.form['search-admin_roles'] = True
    resp = resp.form.submit()
    roles = [el.text_content() for el in resp.pyquery.find('tbody td.name')]
    assert 'Role 1' in roles
    assert 'Manager' in roles
    assert 'Managers of role "simple role"' in roles


@pytest.mark.parametrize('url_name', ('a2-manager-role-parents', 'a2-manager-role-children'))
@pytest.mark.parametrize('sortkey', ('name', 'ou', 'members', 'member', 'via'))
def test_role_members_inheritance_order_by(app, superuser, url_name, sortkey):
    role = Role.objects.create(name='Foobar', ou=get_default_ou())
    url = reverse(url_name, kwargs={'pk': role.pk})
    login(app, superuser)

    app.get(url, params={'sort': sortkey})  # Simple 200 check (see #88249)


def test_role_members_user_role_mixed_table(app, admin, settings, simple_role, simple_user):
    login(app, admin)
    simple_user.roles.add(simple_role)
    url = f'/manage/roles/{simple_role.pk}/'

    resp = app.get(url)

    # no children, directly display members details
    assert resp.forms['search-form']['search-all_members'].value == 'on'
    assert 'disabled' in resp.forms['search-form']['search-all_members'].attrs
    assert 'Download list as CSV' in resp.text

    column_names = [text_content(el) for el in resp.pyquery('table#user-table th') if text_content(el)]
    assert column_names == [
        'User',
        'Username',
        'Email address',
        'First name',
        'Last name',
        'Organizational unit',
        'Direct member',
        'Inherited from',
    ]

    rows = [text_content(el) for el in resp.pyquery('tr td.link')]
    assert rows == ['Jôhn Dôe']

    # add child
    role = Role.objects.create(name='Role a', ou=get_default_ou())
    user = User.objects.create(username='user1', ou=get_default_ou())
    user.roles.add(role)
    simple_role.add_child(role)

    resp = app.get(url)
    assert not resp.forms['search-form']['search-all_members'].value
    assert 'disabled' not in resp.forms['search-form']['search-all_members'].attrs
    assert 'Download list as CSV' not in resp.text

    column_names = [text_content(el) for el in resp.pyquery('table#user-table th') if text_content(el)]
    assert column_names == ['Members']

    rows = [text_content(el) for el in resp.pyquery('tr td.name')]
    assert rows == ['Members of role Role a', 'Jôhn Dôe']

    resp.forms['search-form']['search-all_members'] = True
    resp = resp.forms['search-form'].submit()

    assert resp.forms['search-form']['search-all_members'].value == 'on'
    assert 'disabled' not in resp.forms['search-form']['search-all_members'].attrs
    assert 'Download list as CSV' in resp.text

    rows = [text_content(el) for el in resp.pyquery('tr td.link')]
    assert rows == ['user1', 'Jôhn Dôe']

    resp = resp.click('Add a role as a member')
    assert 'Role a' in resp.text

    # add child role to child
    grandchild = Role.objects.create(name='grandchild')
    role.add_child(grandchild)

    resp = app.get(url)
    rows = [text_content(el) for el in resp.pyquery('tr td.name')]
    assert set(rows) == {'Members of role Role a', 'Members of role grandchild', 'Jôhn Dôe'}

    # remove icon is not shown for indirect child
    assert len(resp.pyquery('tr td a.js-remove-object')) == 2

    # restrict admin to the management of roles
    admin.roles.set([Role.objects.get(slug='_a2-manager-of-roles')])
    resp = app.get(url)
    assert [elt.text() for elt in resp.pyquery('tbody').find('tr td.name').items()] == [
        'Members of role Role a',
        'Members of role grandchild',
        'Jôhn Dôe',
    ]
    assert [elt.text() for elt in resp.pyquery('tbody').find('tr td.name a').items()] == [
        'Members of role Role a',
        'Members of role grandchild',
    ]

    # restrict admin to the management of simple_role
    admin.roles.set([simple_role.get_admin_role()])
    resp = app.get(url)
    assert [elt.text() for elt in resp.pyquery('tbody').find('tr td.name').items()] == [
        'Members of role Role a',
        'Members of role grandchild',
        'Jôhn Dôe',
    ]
    assert [elt.text() for elt in resp.pyquery('tbody').find('tr td.name a').items()] == []


def test_role_members_user_role_mixed_field_choices(
    app, superuser, settings, simple_role, simple_user, role_ou1
):
    url = reverse('a2-manager-role-members', kwargs={'pk': simple_role.pk})
    resp = login(app, superuser, url)

    select2_json = request_select2(app, resp)
    assert len(select2_json['results']) == 10
    assert select2_json['more'] is True

    select2_json = request_select2(app, resp, fetch_all=True)
    assert len(select2_json['results']) == 23
    choices = [x['text'] for x in select2_json['results']]
    assert choices == [
        'Default organizational unit - API clients - Default organizational unit',
        'Default organizational unit - Authenticators - Default organizational unit',
        'Default organizational unit - Managers of role "simple role"',
        'Default organizational unit - Roles - Default organizational unit',
        'Default organizational unit - Services - Default organizational unit',
        'Default organizational unit - Users - Default organizational unit',
        'OU1 - API clients - OU1',
        'OU1 - Authenticators - OU1',
        'OU1 - role_ou1',
        'OU1 - Roles - OU1',
        'OU1 - Services - OU1',
        'OU1 - Users - OU1',
        'Manager',
        'Manager of API clients',
        'Manager of authenticators',
        'Manager of organizational units',
        'Manager of roles',
        'Manager of services',
        'Manager of users',
        'Managers of "Default organizational unit"',
        'Managers of "OU1"',
        'Jôhn Dôe - user@example.net - user',
        'super user - superuser@example.net - superuser',
    ]

    select2_json = request_select2(app, resp, term='user')
    choices = [x['text'] for x in select2_json['results']]
    assert choices == [
        'Default organizational unit - Users - Default organizational unit',
        'OU1 - Users - OU1',
        'Manager of users',
        'Jôhn Dôe - user@example.net - user',
        'super user - superuser@example.net - superuser',
    ]
    assert select2_json['more'] is False

    select2_json = request_select2(app, resp, term='Manager')
    assert len(select2_json['results']) == 10
    select2_json = request_select2(app, resp, term='Manager of')
    assert len(select2_json['results']) == 9
    select2_json = request_select2(app, resp, term='Manager of serv')
    assert len(select2_json['results']) == 1

    for i in range(25):
        Role.objects.create(name=f'test_role_{i}')
    select2_json = request_select2(app, resp, term='test_role_', fetch_all=True)
    assert len(select2_json['results']) == 25

    for i in range(25):
        User.objects.create(username=f'test_user_{i}')
    select2_json = request_select2(app, resp, term='test_user_', fetch_all=True)
    assert len(select2_json['results']) == 25

    for i in range(10):
        Role.objects.create(name=f'test_xxx_{i}')
    User.objects.create(username='test_xxx_10')
    select2_json = request_select2(app, resp, term='test_xxx_')
    assert len(select2_json['results']) == 11


def test_role_members_user_role_add_remove(app, superuser, settings, simple_role, simple_user, role_ou1):
    url = reverse('a2-manager-role-members', kwargs={'pk': simple_role.pk})
    resp = login(app, superuser, url)

    select2_json = request_select2(app, resp, term='Jôhn')
    assert len(select2_json['results']) == 1
    form = resp.forms['add-member']
    form['user_or_role'].force_value(select2_json['results'][0]['id'])
    resp = form.submit().follow()
    assert 'Jôhn Dôe' in resp.text

    select2_json = request_select2(app, resp, term='Jôhn')
    assert len(select2_json['results']) == 0

    data_pks = [row.attrib['data-pk'] for row in resp.pyquery('table tbody tr')]
    assert data_pks == ['user-%s' % simple_user.pk]
    data_pk_args = [row.attrib['data-pk-arg'] for row in resp.pyquery('table tbody tr td a.js-remove-object')]
    assert data_pk_args == ['user_or_role']

    select2_json = request_select2(app, resp, term='role_ou1')
    assert len(select2_json['results']) == 1
    form = resp.forms['add-member']
    form['user_or_role'].force_value(select2_json['results'][0]['id'])
    resp = form.submit().follow()
    assert 'role_ou1' in resp.text

    select2_json = request_select2(app, resp, term='role_ou1')
    assert len(select2_json['results']) == 0

    data_pks = [row.attrib['data-pk'] for row in resp.pyquery('table tbody tr')]
    assert data_pks == ['role-%s' % role_ou1.pk, 'user-%s' % simple_user.pk]
    data_pk_args = [row.attrib['data-pk-arg'] for row in resp.pyquery('table tbody tr td a.js-remove-object')]
    assert data_pk_args == ['user_or_role', 'user_or_role']

    # simulate click on Jôhn Dôe delete icon
    token = str(resp.context['csrf_token'])
    params = {'action': 'remove', 'user_or_role': 'user-%s' % simple_user.pk, 'csrfmiddlewaretoken': token}
    resp = app.post(
        '/manage/roles/%s/' % simple_role.pk, params=params, headers={'Referer': 'https://testserver/'}
    ).follow()
    assert 'Jôhn Dôe' not in resp.text

    # simulate click on role_ou1 delete icon
    token = str(resp.context['csrf_token'])
    params = {'action': 'remove', 'user_or_role': 'role-%s' % role_ou1.pk, 'csrfmiddlewaretoken': token}
    resp = app.post(
        '/manage/roles/%s/' % simple_role.pk, params=params, headers={'Referer': 'https://testserver/'}
    ).follow()
    assert 'role_ou1' not in resp.text

    # invalid choices are ignored
    for invalid_choice in ('', 'wrong-wrong', 'user-', 'user-xxx', 'role', 'user-99999'):
        form = resp.forms['add-member']
        form['user_or_role'].force_value(invalid_choice)
        resp = form.submit().maybe_follow()


def test_role_members_select2(app, superuser, simple_user, settings):
    assert superuser.ou is None and simple_user.ou == get_default_ou()
    r = Role.objects.create(name='role', slug='role', ou=get_default_ou())
    url = reverse('a2-manager-role-members', kwargs={'pk': r.pk})

    response = login(app, superuser, url)

    select2_url = response.pyquery('select')[0].attrib['data-ajax--url']
    select2_response = app.get(select2_url, expect_errors=True)
    assert select2_response.status_code == 400


def test_role_table_ordering(app, admin):
    Role.objects.create(name='a role')
    Role.objects.create(name='bD role')
    Role.objects.create(name='A role', slug='a-role-2')
    Role.objects.create(name='Z role')
    Role.objects.create(name='É role')
    Role.objects.create(name='Bc role')

    resp = login(app, admin, '/manage/roles/')
    assert [x.text for x in resp.pyquery('td.name a')] == [
        'a role',
        'A role',
        'Bc role',
        'bD role',
        'É role',
        'Z role',
    ]


def test_manager_view_admin_role(app, admin, simple_role):
    login(app, admin)

    resp = app.get('/manage/roles/%s/' % simple_role.get_admin_role().pk)
    assert 'Managers of role &quot;simple role&quot;' in resp.text
    assert 'This role is technical, you cannot delete it.' in resp.text


def test_role_summary_identity_management(app, superuser, settings, simple_role):
    url = reverse('a2-manager-role-summary', kwargs={'pk': simple_role.pk})

    resp = login(app, superuser, url)
    assert "This role doesn't contain permissions of any other role." in resp.text

    for i in range(10):
        role = Role.objects.create(name=f'Role {i}', ou=get_default_ou())
        simple_role.add_parent(role)

    for i in range(10):
        role = Role.objects.create(name=f'Admin {i}', ou=get_default_ou())
        simple_role.get_admin_role().add_child(role)

    resp = app.get(url)
    assert "This role doesn't contain permissions of any other role." not in resp.text

    for i, el in enumerate(resp.pyquery.find('div.parents a.role-inheritance-parent')):
        assert el.text == f'Role {i}'

    for i, el in enumerate(resp.pyquery.find('div.administred a.role-inheritance-parent')):
        assert el.text == f'Foo {i}'


def test_role_summary_other_services(app, superuser, settings, simple_role, monkeypatch):
    parent_role = Role.objects.create(name='parent role', slug='parent-role', ou=get_default_ou())
    simple_role.add_parent(parent_role)

    roles_summary_cache = {
        simple_role.uuid: {
            'name': 'simple role',
            'slug': 'simple-role',
            'parents': [parent_role.uuid],
            'type_objects': [
                {
                    'hit': [
                        {
                            'id': 'foo',
                            'text': 'Foo',
                            'type': 'forms',
                            'urls': {
                                'dependencies': 'http://example.org/api/export-import/forms/foo/dependencies/',
                                'redirect': 'http://example.org/api/export-import/forms/foo/redirect/',
                            },
                        }
                    ],
                    'id': 'forms',
                    'singular': 'Formulaire',
                    'text': 'Formulaires',
                    'urls': {'list': 'http://example.org/api/export-import/forms/'},
                },
                {
                    'hit': [
                        {
                            'id': 'test',
                            'text': 'Test',
                            'type': 'workflows',
                            'urls': {
                                'dependencies': 'http://example.org/api/export-import/workflows/test/dependencies/',
                                'redirect': 'http://example.org/api/export-import/workflows/test/redirect/',
                            },
                        }
                    ],
                    'id': 'workflows',
                    'singular': 'Workflow',
                    'text': 'Workflows',
                    'urls': {'list': 'http://example.org/api/export-import/workflows/'},
                },
            ],
            'parents_type_objects': [
                {
                    'hit': [
                        {
                            'id': 'bar',
                            'text': 'Bar',
                            'type': 'forms',
                            'urls': {
                                'dependencies': 'http://example.org/api/export-import/forms/bar/dependencies/',
                                'redirect': 'http://example.org/api/export-import/forms/bar/redirect/',
                            },
                        }
                    ],
                    'id': 'forms',
                    'singular': 'Formulaire',
                    'text': 'Formulaires',
                    'urls': {'list': 'http://example.org/api/export-import/forms/'},
                }
            ],
        },
    }

    def mock_get_roles_summary_cache():
        return roles_summary_cache

    import authentic2.manager.role_views

    monkeypatch.setattr(
        authentic2.manager.role_views, 'get_roles_summary_cache', mock_get_roles_summary_cache
    )

    url = reverse('a2-manager-role-summary', kwargs={'pk': simple_role.pk})
    resp = login(app, superuser, url)

    details = resp.pyquery.find('div#direct-usage details')
    assert len(details) == 2
    forms_details = details[0]
    assert forms_details.find('summary').text == 'Formulaires'
    form_anchor = forms_details.find('ul').find('li').find('a')
    assert form_anchor.text == 'Foo'
    assert form_anchor.attrib['href'] == 'http://example.org/api/export-import/forms/foo/redirect/'

    workflows_details = details[1]
    assert workflows_details.find('summary').text == 'Workflows'
    workflow_anchor = workflows_details.find('ul').find('li').find('a')
    assert workflow_anchor.text == 'Test'
    assert workflow_anchor.attrib['href'] == 'http://example.org/api/export-import/workflows/test/redirect/'

    indirect_details = resp.pyquery.find('div#indirect-usage details')
    assert len(indirect_details) == 1
    forms_details = indirect_details[0]
    assert forms_details.find('summary').text == 'Formulaires'
    form_anchor = forms_details.find('ul').find('li').find('a')
    assert form_anchor.text == 'Bar'
    assert form_anchor.attrib['href'] == 'http://example.org/api/export-import/forms/bar/redirect/'

    assert not resp.pyquery.find('div.errornotice')

    roles_summary_cache['error'] = 'BOOM!'
    resp = app.get(url)
    assert resp.pyquery.find('div.errornotice').text() == 'BOOM!'


def test_role_summary_in_nav(app, superuser, simple_role):
    url = reverse('a2-manager-role-members', kwargs={'pk': simple_role.pk})
    resp = login(app, superuser, url)
    lis = resp.pyquery.find('ul.extra-actions-menu li')
    anchor = lis[2].find('a')
    assert anchor.text == 'Summary page'
    assert anchor.attrib['href'] == '/manage/roles/%s/summary/' % simple_role.pk
