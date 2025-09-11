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

import base64
import io
import operator
import os
import uuid
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType

from authentic2.a2_rbac import models
from authentic2.a2_rbac.utils import get_operation
from authentic2.manager.user_import import Report, UserImport
from authentic2.models import Attribute
from authentic2.models import UserImport as UserImportModel
from authentic2.utils.crypto import new_base64url_id

from .utils import login


@pytest.fixture
def profile(transactional_db):
    Attribute.objects.create(name='phone', kind='phone_number', label='Numéro de téléphone')


def test_user_import(transactional_db, profile):
    from authentic2.manager import user_import

    with patch.object(user_import, '_report_publik_provisionning') as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = None

        content = '''email key verified,first_name,last_name,phone no-create
    tnoel@entrouvert.com,Thomas,Noël,0123456789
    fpeters@entrouvert.com,Frédéric,Péters,+3281123456
    x,x,x,x'''
        fd = io.BytesIO(content.encode('utf-8'))

        assert len(list(UserImport.all())) == 0

        UserImport.new(fd, encoding='utf-8')
        UserImport.new(fd, encoding='utf-8')

        assert len(list(UserImport.all())) == 2
        for user_import in UserImport.all():
            with user_import.import_file as fd:
                assert fd.read() == content.encode('utf-8')

        for user_import in UserImport.all():
            report = Report.new(user_import)
            assert user_import.reports[report.uuid].exists()
            assert user_import.reports[report.uuid].data['encoding'] == 'utf-8'
            assert user_import.reports[report.uuid].data['state'] == 'waiting'

            t = report.run(start=False)
            t.start()
            t.join()

            mock_ctx.assert_called_once()
            mock_ctx.assert_called_with(False)
            mock_ctx.reset_mock()

            assert user_import.reports[report.uuid].data['state'] == 'finished'
            assert user_import.reports[report.uuid].data['importer']
            assert not user_import.reports[report.uuid].data['importer'].errors

        for user_import in UserImport.all():
            reports = list(user_import.reports)
            assert len(reports) == 1
            assert reports[0].created
            importer = reports[0].data['importer']
            assert importer.rows[0].is_valid
            assert importer.rows[1].is_valid
            assert not importer.rows[2].is_valid

        user_imports = sorted(UserImport.all(), key=operator.attrgetter('created'))
        user_import1 = user_imports[0]
        report1 = list(user_import1.reports)[0]
        importer = report1.data['importer']
        assert all(row.action == 'create' for row in importer.rows[:2])
        assert all(cell.action == 'updated' for row in importer.rows[:2] for cell in row.cells[:3])
        assert all(cell.action == 'nothing' for row in importer.rows[:2] for cell in row.cells[3:])

        user_import2 = user_imports[1]
        report2 = list(user_import2.reports)[0]
        importer = report2.data['importer']
        assert all(row.action == 'update' for row in importer.rows[:2])
        assert all(cell.action == 'nothing' for row in importer.rows[:2] for cell in row.cells[:3])
        assert all(cell.action == 'updated' for row in importer.rows[:2] for cell in row.cells[3:])


def test_user_import_roles(transactional_db, profile, user_ou1, ou1):
    role1 = models.Role.objects.create(name='Role 1', slug='role-1', ou=ou1)
    role2 = models.Role.objects.create(name='Role 2', slug='role-2', ou=ou1)

    user_ou1.roles.add(role2)

    content = f'''email key verified,first_name,last_name,_role_slug clear
tnoel@entrouvert.com,Thomas,Noël,{role1.slug}
{user_ou1.email},{user_ou1.first_name},{user_ou1.last_name},{role1.slug}'''
    fd = io.BytesIO(content.encode('utf-8'))

    assert len(list(UserImport.all())) == 0

    user_import = UserImport.new(fd, encoding='utf-8')
    user_import.meta['ou'] = ou1
    report = Report.new(user_import)

    # this user is not allowed to manage users but it's ok.
    # permissions are checked in the view for that. we only checks roles changes here
    t = report.run(start=False, simulate=True, user=user_ou1)
    t.start()
    t.join()
    importer = report.data['importer']

    # we are not allowed to manage roles
    for row in importer.rows:
        assert row.cells[-1].errors[0].code == 'role-unauthorized'

    # allow user_ou1 to manage members of role1 via role2
    perm = models.Permission.objects.create(
        operation=get_operation(models.MANAGE_MEMBERS_OP),
        target_ct=ContentType.objects.get_for_model(models.Role),
        target_id=role1.pk,
    )
    role2.permissions.add(perm)

    # clear perms cache
    del user_ou1._rbac_perms_cache

    report = Report.new(user_import)
    t = report.run(start=False, simulate=True, user=user_ou1)
    t.start()
    t.join()
    importer = report.data['importer']

    # we are now allowed to manage role1
    assert not importer.rows[0].cells[-1].errors

    # we cant clear role2
    error = 'You are not allowed to clear roles for this user'
    assert importer.rows[1].cells[-1].errors[0].description == error

    # real run, same result
    report = Report.new(user_import)
    t = report.run(start=False, user=user_ou1)
    t.start()
    t.join()
    importer = report.data['importer']
    assert not importer.rows[0].cells[-1].errors
    error = 'You are not allowed to clear roles for this user'
    assert importer.rows[1].cells[-1].errors[0].description == error


def old_import_uuid():
    return base64.b32encode(uuid.uuid4().bytes).strip(b'=').lower().decode('ascii')


def test_manager_user_import_display(app, db, ou1, superuser):
    all_uuids = []
    import_files = []
    for dummy in range(20):
        model_instance = UserImportModel.objects.create(uuid=old_import_uuid(), ou=ou1)
        user_import = UserImport(uuid=model_instance.uuid)
        import_files.append(user_import.path)
        all_uuids.append(model_instance.uuid)

    for dummy in range(5):
        model_instance = UserImportModel.objects.create(uuid=new_base64url_id(), ou=ou1)
        user_import = UserImport(uuid=model_instance.uuid)
        import_files.append(user_import.path)
        all_uuids.append(model_instance.uuid)

    for fname in import_files:
        with open(fname, 'w+'):
            pass

    response = login(app, superuser, '/manage/users/import/')

    displayed_uuids = sorted(
        [elt.attrib['data-uuid'] for elt in response.pyquery('table.main.left:first tbody tr')]
    )
    assert displayed_uuids == sorted(all_uuids)

    for fname in import_files:
        os.unlink(fname)
