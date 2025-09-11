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


import codecs
import io
import sys

import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.core import mail
from django.utils.html import escape

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.journal.models import Event
from authentic2.csv_import import CsvHeader, CsvImporter, Error, LineError, UserCsvImporter
from authentic2.custom_user.models import User
from authentic2.models import Attribute, PasswordReset
from authentic2.utils.crypto import new_base64url_id

ENCODINGS = [
    'iso-8859-1',
    'iso-8859-15',
    'utf-8',
    'cp1252',
]


def parametrize_encodings(func):
    return pytest.mark.parametrize('encoding', ENCODINGS, indirect=True)(func)


@pytest.fixture
def style(request):
    return 'file'


@pytest.fixture
def encoding(request):
    if getattr(request, 'param', None):
        return request.param
    return 'utf8'


@pytest.fixture
def profile(db):
    Attribute.objects.create(name='phone', kind='phone_number', label='Numéro de téléphone')


@pytest.fixture
def csv_importer_factory(encoding, style):
    def factory(content):
        content = content.encode(encoding)
        if style == 'file':
            content = io.BytesIO(content)
        importer = CsvImporter()
        run = importer.run
        importer.run = lambda *args, **kwargs: run(content, *args, encoding=encoding, **kwargs)
        return importer

    return factory


@pytest.fixture
def user_csv_importer_factory(encoding, style):
    def factory(content):
        content = content.encode(encoding)
        if style == 'file':
            content = io.BytesIO(content)
        importer = UserCsvImporter(new_base64url_id(), new_base64url_id())
        run = importer.run
        importer.run = lambda *args, **kwargs: run(content, *args, encoding=encoding, **kwargs)
        return importer

    return factory


def check_journal_messages(expected_messages):
    evts = [evt.message.split('</a> ')[1] for evt in Event.objects.order_by('timestamp', 'id')]
    assert evts == [escape(msg) for msg in expected_messages]


def test_unknown_csv_dialect_error(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('unknown-csv-dialect')]


def test_bad_csv_encoding(profile):
    importer = CsvImporter()
    assert not importer.run('é'.encode(), 'ascii')
    assert importer.error == Error('bad-encoding')


@pytest.mark.skipif(sys.version_info >= (3, 11), reason='python 3.11 csv module handles null bytes')
def test_null_byte(profile):
    importer = CsvImporter()
    assert not importer.run(b'email key,first_name\n1,\x00', 'ascii')
    assert importer.error == Error('csv-read-error')

    importer = CsvImporter()
    assert not importer.run(b'\x00', 'ascii')
    assert importer.error == Error('csv-read-error')


def test_empty_header_row_error(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('\n1,2,3')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('empty-header-row')]


def test_unknown_or_missing_attribute_error1(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('email key,first_name," "\n1,2,3')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [LineError('unknown-or-missing-attribute', line=1, column=2)]


def test_unknown_or_missing_attribute_error2(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('email key,first_name,x\n1,2,3')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [LineError('unknown-or-missing-attribute', line=1, column=3)]


def test_unknown_flag_error(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('email key,first_name xxx\n1,2')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [LineError('unknown-flag', line=1, column=2)]


def test_missing_key_column_error(profile, user_csv_importer_factory, settings):
    content = 'email,first_name\ntnoel@entrouvert.com,Thomas'
    importer = user_csv_importer_factory(content)
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('missing-key-column')]

    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    importer = user_csv_importer_factory(content)
    assert importer.run()
    assert not importer.has_errors

    content = 'username,first_name\ntnoel,Thomas'

    importer = user_csv_importer_factory(content)
    assert importer.run()
    assert not importer.has_errors

    settings.A2_USERNAME_IS_UNIQUE = False
    importer = user_csv_importer_factory(content)
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('missing-key-column')]

    content = 'last_name,first_name\nNoel,Thomas'
    importer = user_csv_importer_factory(content)
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('missing-key-column')]


def test_too_many_key_columns_error(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('email key,first_name key\n1,2')
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors == [Error('too-many-key-columns')]


def test_bom_character(profile, user_csv_importer_factory):
    content = codecs.BOM_UTF8 + b'email key,first_name\ntest@entrouvert.org,hop'
    file_content = io.BytesIO(content)
    importer = UserCsvImporter('userimportuuid', 'reportuuid')
    assert importer.run(file_content, 'utf-8-sig')
    assert not importer.has_errors


@parametrize_encodings
@pytest.mark.parametrize('style', ['str', 'file'], indirect=True)
def test_run(profile, user_csv_importer_factory):
    assert User.objects.count() == 0
    content = '''email key,first_name,last_name,phone update
tnoel@entrouvert.com,Thomas,Noël,0123456789
fpeters@entrouvert.com,Frédéric,Péters,+3281005678
x,x,x,x'''
    importer = user_csv_importer_factory(content)

    assert importer.run(), importer.errors
    assert importer.headers == [
        CsvHeader(1, 'email', field=True, key=True, verified=True),
        CsvHeader(2, 'first_name', field=True),
        CsvHeader(3, 'last_name', field=True),
        CsvHeader(4, 'phone', field=False, attribute=True),
    ]
    assert importer.has_errors
    assert len(importer.rows) == 3
    assert all(row.is_valid for row in importer.rows[:2])
    assert not importer.rows[2].is_valid
    assert importer.rows[2].cells[0].errors
    assert all(error == Error('data-error') for error in importer.rows[2].cells[0].errors)
    assert not importer.rows[2].cells[1].errors
    assert not importer.rows[2].cells[2].errors
    assert importer.rows[2].cells[3].errors
    assert all(error == Error('data-error') for error in importer.rows[2].cells[3].errors)

    assert importer.updated == 0
    assert importer.created == 2

    assert User.objects.count() == 2
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert thomas.ou == get_default_ou()
    assert thomas.email_verified is True
    assert thomas.first_name == 'Thomas'
    assert thomas.attributes.first_name == 'Thomas'
    assert thomas.last_name == 'Noël'
    assert thomas.attributes.last_name == 'Noël'
    # phonenumbers' e.164 representation from a settings.DEFAULT_COUNTRY_CODE dial:
    assert thomas.attributes.phone == '+33123456789'
    assert thomas.password

    fpeters = User.objects.get(email='fpeters@entrouvert.com')
    assert fpeters.ou == get_default_ou()
    assert fpeters.first_name == 'Frédéric'
    assert fpeters.email_verified is True
    assert fpeters.attributes.first_name == 'Frédéric'
    assert fpeters.last_name == 'Péters'
    assert fpeters.attributes.last_name == 'Péters'
    # phonenumbers' e.164 representation from a settings.DEFAULT_COUNTRY_CODE dial:
    assert fpeters.attributes.phone == '+3281005678'


def test_simulate(profile, user_csv_importer_factory):
    assert User.objects.count() == 0
    content = '''email key,first_name,last_name,phone update
tnoel@entrouvert.com,Thomas,Noël,0123456789
fpeters@entrouvert.com,Frédéric,Péters,+3281005678
x,x,x,x'''
    importer = user_csv_importer_factory(content)

    assert importer.run(simulate=True), importer.errors
    assert importer.headers == [
        CsvHeader(1, 'email', field=True, key=True, verified=True),
        CsvHeader(2, 'first_name', field=True),
        CsvHeader(3, 'last_name', field=True),
        CsvHeader(4, 'phone', field=False, attribute=True),
    ]
    assert importer.has_errors
    assert len(importer.rows) == 3
    assert all(row.is_valid for row in importer.rows[:2])
    assert not importer.rows[2].is_valid
    assert importer.rows[2].cells[0].errors
    assert all(error == Error('data-error') for error in importer.rows[2].cells[0].errors)
    assert not importer.rows[2].cells[1].errors
    assert not importer.rows[2].cells[2].errors
    assert importer.rows[2].cells[3].errors
    assert all(error == Error('data-error') for error in importer.rows[2].cells[3].errors)

    assert importer.updated == 0
    assert importer.created == 2

    assert User.objects.count() == 0


def test_create_unique_error(profile, user_csv_importer_factory):
    content = '''email key verified,first_name,last_name,phone unique
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create(ou=get_default_ou())
    user.attributes.phone = '+33123456789'

    assert importer.run()

    assert importer.created == 0
    assert importer.updated == 0
    assert len(importer.rows) == 1
    assert not importer.rows[0].is_valid
    assert importer.rows[0].action == 'create'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(not cell.action for cell in importer.rows[0])
    assert importer.rows[0].errors == [Error('unique-constraint-failed')]


def test_create_unique_in_ou(profile, user_csv_importer_factory):
    content = '''email key verified,first_name,last_name,phone unique
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create()
    user.attributes.phone = '+33123456789'

    assert importer.run()

    assert len(importer.rows) == 1
    assert importer.rows[0].is_valid
    assert importer.rows[0].action == 'create'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(cell.action == 'updated' for cell in importer.rows[0])
    assert importer.created == 1
    assert importer.updated == 0


def test_create_unique_globally_error(profile, user_csv_importer_factory):
    content = '''email key verified,first_name,last_name,phone globally-unique
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create()
    user.attributes.phone = '+33123456789'

    assert importer.run()

    assert importer.created == 0
    assert importer.updated == 0
    assert len(importer.rows) == 1
    assert not importer.rows[0].is_valid
    assert importer.rows[0].action == 'create'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(not cell.action for cell in importer.rows[0])
    assert importer.rows[0].errors == [Error('unique-constraint-failed')]


def test_create_key_self_reference_error(profile, user_csv_importer_factory):
    content = '''email key,first_name,last_name,phone
tnoel@entrouvert.com,Thomas,Noël,0606060606
tnoel@entrouvert.com,Frédéric,Péters,+3281123456'''
    importer = user_csv_importer_factory(content)

    assert importer.run()

    assert importer.created == 1
    assert importer.updated == 0
    assert len(importer.rows) == 2
    assert importer.rows[0].is_valid
    assert importer.rows[0].action == 'create'
    assert not importer.rows[1].is_valid
    assert importer.rows[1].action == 'update'
    assert importer.rows[1].errors == [Error('unique-constraint-failed')]


def test_update_unique_error(profile, user_csv_importer_factory):
    content = '''email key verified,first_name,last_name,phone unique update
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create(ou=get_default_ou())
    user.attributes.phone = '+33123456789'

    user = User.objects.create(email='tnoel@entrouvert.com', ou=get_default_ou())

    assert importer.run()

    assert importer.created == 0
    assert importer.updated == 0
    assert len(importer.rows) == 1
    assert not importer.rows[0].is_valid
    assert importer.rows[0].action == 'update'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(not cell.action for cell in importer.rows[0])
    assert importer.rows[0].errors == [Error('unique-constraint-failed')]


def test_update_unique_globally_error(profile, user_csv_importer_factory):
    content = '''email key verified,first_name,last_name,phone globally-unique update
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create()
    user.attributes.phone = '+33123456789'

    User.objects.create(email='tnoel@entrouvert.com', ou=get_default_ou())

    assert importer.run()

    assert importer.created == 0
    assert importer.updated == 0
    assert len(importer.rows) == 1
    assert not importer.rows[0].is_valid
    assert importer.rows[0].action == 'update'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(not cell.action for cell in importer.rows[0])
    assert importer.rows[0].errors == [Error('unique-constraint-failed')]


def test_update_unique_globally(profile, user_csv_importer_factory):
    content = '''email key verified no-update,first_name no-update,last_name no-update,phone unique update
tnoel@entrouvert.com,Thomas,Noël,0123456789'''
    importer = user_csv_importer_factory(content)

    user = User.objects.create()
    user.attributes.phone = '+33123456789'

    thomas = User.objects.create(email='tnoel@entrouvert.com', ou=get_default_ou())

    assert importer.run()

    assert importer.created == 0
    assert importer.updated == 1
    assert len(importer.rows) == 1
    assert importer.rows[0].is_valid
    assert importer.rows[0].action == 'update'
    assert all(not cell.errors for cell in importer.rows[0])
    assert all(cell.action == 'nothing' for cell in importer.rows[0].cells[:3])
    assert importer.rows[0].cells[3].action == 'updated'

    thomas.refresh_from_db()
    assert not thomas.first_name
    assert not thomas.last_name
    assert thomas.attributes.phone == '+33123456789'


def test_external_id(profile, user_csv_importer_factory):
    assert User.objects.count() == 0
    content = '''_source_name,_source_id,email,first_name,last_name,phone
app1,1,tnoel@entrouvert.com,Thomas,Noël,0606060606
app1,2,tnoel@entrouvert.com,Thomas,Noël,0606060606
'''
    importer = user_csv_importer_factory(content)

    assert importer.run(), importer.errors
    assert importer.headers == [
        CsvHeader(1, '_source_name'),
        CsvHeader(2, '_source_id', key=True),
        CsvHeader(3, 'email', field=True, verified=True),
        CsvHeader(4, 'first_name', field=True),
        CsvHeader(5, 'last_name', field=True),
        CsvHeader(6, 'phone', field=False, attribute=True),
    ]
    assert not importer.has_errors
    assert len(importer.rows) == 2
    for external_id in ['1', '2']:
        thomas = User.objects.get(userexternalid__source='app1', userexternalid__external_id=external_id)

        assert thomas.ou == get_default_ou()
        assert thomas.email_verified is True
        assert thomas.first_name == 'Thomas'
        assert thomas.attributes.first_name == 'Thomas'
        assert thomas.last_name == 'Noël'
        assert thomas.attributes.last_name == 'Noël'
        assert thomas.attributes.phone == '+33606060606'

    importer = user_csv_importer_factory(content)
    assert importer.run(), importer.errors
    assert not importer.has_errors


def test_user_roles_csv(profile, user_csv_importer_factory):
    role_name = 'test_name'
    role_slug = 'test_slug'
    role = Role.objects.create(name=role_name, slug=role_slug, ou=get_default_ou())
    role2 = Role.objects.create(name='test2', ou=get_default_ou())
    base_header = 'email key,first_name,last_name,phone,'
    base_user = 'tnoel@entrouvert.com,Thomas,Noël,0123456789,'

    content_name_add = '\n'.join((base_header + '_role_name', base_user + role_name))
    Event.objects.all().delete()
    importer = user_csv_importer_factory(content_name_add)
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert thomas in role.members.all()
    check_journal_messages(
        [
            'import started',
            'user Thomas Noël create',
            'user Thomas Noël update property email : "tnoel@entrouvert.com"',
            'user Thomas Noël set email verified',
            'user Thomas Noël update property first_name : "Thomas"',
            'user Thomas Noël update property last_name : "Noël"',
            'user Thomas Noël update attribute phone : "+33123456789"',
            'user Thomas Noël add role : "test_name"',
            'import ended',
        ]
    )

    thomas.roles.add(role2)

    Event.objects.all().delete()
    importer = user_csv_importer_factory(content_name_add)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role2.members.all()
    # No changes, nothing logged
    check_journal_messages(['import started', 'import ended'])

    content_name_delete = '\n'.join((base_header + '_role_name delete', base_user + role_name))
    Event.objects.all().delete()
    importer = user_csv_importer_factory(content_name_delete)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas not in role.members.all()
    assert thomas in role2.members.all()
    check_journal_messages(['import started', 'user Thomas Noël remove role : "test_name"', 'import ended'])

    Event.objects.all().delete()
    importer = user_csv_importer_factory(content_name_delete)
    assert importer.run()
    # No changes, nothing logged
    check_journal_messages(['import started', 'import ended'])

    content_name_clear = '\n'.join((base_header + '_role_name clear', base_user + role_name))
    importer = user_csv_importer_factory(content_name_clear)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role.members.all()
    assert thomas not in role2.members.all()

    thomas.roles.remove(role)
    content_name_add_multiple = '\n'.join(
        (base_header + '_role_name', base_user + role_name, base_user + 'test2')
    )
    importer = user_csv_importer_factory(content_name_add_multiple)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role.members.all()
    assert thomas in role2.members.all()

    thomas.roles.remove(role)
    thomas.roles.remove(role2)
    content_name_clear_multiple = '\n'.join(
        (base_header + '_role_name clear', base_user + role_name, base_user + 'test2')
    )
    importer = user_csv_importer_factory(content_name_clear_multiple)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role.members.all()
    assert thomas in role2.members.all()

    thomas.roles.remove(role)
    content_slug_add = '\n'.join((base_header + '_role_slug', base_user + role_slug))
    importer = user_csv_importer_factory(content_slug_add)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role.members.all()

    thomas.roles.remove(role)
    content_only_key = '''email key,_role_name
tnoel@entrouvert.com,test_name'''
    importer = user_csv_importer_factory(content_only_key)
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas in role.members.all()

    content_name_error = '\n'.join((base_header + '_role_name', base_user + 'bad_name'))
    importer = user_csv_importer_factory(content_name_error)
    assert importer.run()
    assert importer.has_errors
    assert importer.rows[0].cells[-1].errors[0].code == 'role-not-found'

    content_header_error = '\n'.join(
        (base_header + '_role_name,_role_slug', base_user + ','.join((role_name, role_slug)))
    )
    importer = user_csv_importer_factory(content_header_error)
    assert not importer.run()
    assert importer.has_errors
    assert importer.errors[0].code == 'invalid-role-column'

    # empty role name doesn't raise error
    content_name_error = '\n'.join((base_header + '_role_name', base_user + ''))
    importer = user_csv_importer_factory(content_name_error)
    assert importer.run()
    assert not importer.has_errors


def test_strip_cell_values(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory(
        'email key,first_name,@registration\ntest@entrouvert.org ,hop, send-email '
    )
    assert importer.run(), importer.errors
    assert not importer.has_errors
    cells = importer.rows[0].cells
    assert [c.value for c in cells] == ['test@entrouvert.org', 'hop', 'send-email']


def test_ignore_empty_rows(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory(
        'email key,first_name,@registration\ntest@entrouvert.org,hop,send-email\n\n'
    )
    assert importer.run(), importer.errors
    assert not importer.has_errors, importer.errors
    cells = importer.rows[0].cells
    assert [c.value for c in cells] == ['test@entrouvert.org', 'hop', 'send-email']
    assert len(importer.rows) == 1


def test_csv_registration_options(profile, user_csv_importer_factory):
    content = '''email key,first_name,last_name,@registration
tnoel@entrouvert.com,Thomas,Noël,'''

    importer = user_csv_importer_factory(content + 'send-email')
    assert importer.run(simulate=True)
    assert not mail.outbox

    importer = user_csv_importer_factory(content + 'send-email')
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert len(mail.outbox) == 1
    assert 'https://testserver/password/reset/confirm/' in mail.outbox[0].body

    password = thomas.password
    del mail.outbox[0]
    importer = user_csv_importer_factory(content + 'send-email')
    assert importer.run()
    thomas.refresh_from_db()
    assert thomas.password == password
    assert not mail.outbox

    importer = user_csv_importer_factory(content + 'invalid-option')
    assert importer.run()
    assert importer.has_errors
    assert importer.rows[0].cells[-1].errors[0].code == 'data-error'


def test_csv_force_password_reset(profile, user_csv_importer_factory):
    importer = user_csv_importer_factory('email key,first_name,last_name\ntnoel@entrouvert.com,Thomas,Noël')
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert not PasswordReset.objects.filter(user=thomas).exists()

    csv = 'email key,first_name,last_name,@force-password-reset\ntnoel@entrouvert.com,Thomas,Noël,'
    importer = user_csv_importer_factory(csv)
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert not PasswordReset.objects.filter(user=thomas).exists()

    importer = user_csv_importer_factory(csv + 'false')
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert not PasswordReset.objects.filter(user=thomas).exists()

    importer = user_csv_importer_factory(csv + 'true')
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert PasswordReset.objects.filter(user=thomas).exists()

    importer = user_csv_importer_factory(csv + 'any other value')
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert PasswordReset.objects.filter(user=thomas).exists()


def test_check_empty_value_for_uniqueness(profile, user_csv_importer_factory):
    ou = get_default_ou()
    ou.username_is_unique = True
    ou.email_is_unique = True
    ou.save()

    Attribute.objects.update(required=False)
    User.objects.create(username='john.doe', email='john.doe@gmail.com')

    content = '''_source_name,_source_id,username,email,first_name,last_name
remote,1,,john.doe1@gmail.com,John,Doe1
remote,2,john.doe2,,John,Doe2
remote,3,,,John,Doe3
remote,4,,,John,Doe4
remote,5,,john.doe1@gmail.com,,
remote,6,john.doe2,,,
remote,7,,john.doe@gmail.com,,
remote,8,john.doe,,,
'''
    for i, line in enumerate(content.splitlines()):
        print(i, line.count(','))
    importer = user_csv_importer_factory(content)
    importer.run()
    assert importer.has_errors
    assert not importer.errors
    assert importer.headers_by_name['username'].unique
    assert importer.headers_by_name['username'].globally_unique
    assert importer.headers_by_name['email'].unique
    assert not importer.headers_by_name['email'].globally_unique
    row_errors = {
        row.line: [error.description for error in row.errors] for row in importer.rows if row.errors
    }
    assert row_errors == {
        6: [
            'Unique constraint on column "email" failed: value already appear on line 2',
            'Unique constraint on column "email" failed',
        ],
        7: ['Unique constraint on column "username" failed: value already appear on line 3'],
        9: ['Unique constraint on column "username" failed'],
    }
    cell_errors = {
        row.line: {cell.header.name: [error for error in cell.errors] for cell in row.cells if cell.errors}
        for row in importer.rows
        if any(cell.errors for cell in row.cells)
    }
    assert cell_errors == {}


def test_csv_password_hash(profile, user_csv_importer_factory):
    content = '''email key,first_name,last_name,password_hash
tnoel@entrouvert.com,Thomas,Noël,%s'''
    password_hash = make_password('hop')

    importer = user_csv_importer_factory(content % password_hash)
    assert importer.run()
    thomas = User.objects.get(email='tnoel@entrouvert.com')
    assert check_password('hop', thomas.password)

    password_hash = make_password('test', hasher='pbkdf2_sha256')
    importer = user_csv_importer_factory(content % password_hash)
    assert importer.run()
    thomas.refresh_from_db()
    assert check_password('test', thomas.password)

    importer = user_csv_importer_factory(content % 'wrong-format')
    assert importer.run()
    assert importer.has_errors
    assert 'Unknown hashing algorithm' in importer.rows[0].cells[-1].errors[0].description

    importer = user_csv_importer_factory(content)
    assert importer.run()
    assert importer.has_errors
    assert 'Unknown hashing algorithm' in importer.rows[0].cells[-1].errors[0].description


def test_csv_password_hash_invalid(profile, user_csv_importer_factory):
    content = '''email key,first_name,last_name,password_hash
tnoel@entrouvert.com,Thomas,Noël,pbkdf2_sha256$3600$jqd2OrcMU6dPk+nRQIOt/gZI+cTWqvpYYEmCks5m2/w='''

    importer = user_csv_importer_factory(content)
    assert importer.run()
    assert importer.has_errors
    assert 'Invalid format for' in importer.rows[0].cells[-1].errors[0].description


def test_same_key_different_ou(db, user_csv_importer_factory):
    ou_agent = OU.objects.create(name='Agent', slug='agent')

    user1 = User.objects.create(
        email='john.doe@example.com', first_name='John', last_name='Doe', ou=get_default_ou()
    )
    user2 = User.objects.create(email='john.doe@example.com', first_name='John', last_name='Doe', ou=ou_agent)
    assert User.objects.count() == 2

    content = '''email key,first_name,last_name update
john.doe@example.com,John,Doe2'''
    importer = user_csv_importer_factory(content)

    importer.run(ou=ou_agent)
    assert not importer.has_errors, importer.errors
    assert User.objects.count() == 2
    user1.refresh_from_db()
    user2.refresh_from_db()
    assert user1.last_name == 'Doe'
    assert user2.last_name == 'Doe2'


def test_email_case_insensitive(profile, user_csv_importer_factory, settings):
    assert not User.objects.create(ou=get_default_ou(), email='JOHN.DOE@EXAMPLE.COM').first_name
    content = 'email key,first_name\njohn.doe@example.com,Thomas'
    importer = user_csv_importer_factory(content)
    assert importer.run(), importer.errors
    assert User.objects.count() == 1
    assert User.objects.get().first_name == 'Thomas'
