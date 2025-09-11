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

import builtins as __builtin__
import json
import random

import pytest
from django import VERSION
from django.core import management
from django.core.exceptions import ValidationError

from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou


@pytest.fixture
def json_fixture(tmpdir):
    def f(content):
        name = str(random.getrandbits(64))
        outfile = tmpdir.join(name)
        with outfile.open('w') as f:
            f.write(json.dumps(content))
        return outfile.strpath

    return f


def dummy_export_site(*args):
    return {'roles': [{'name': 'role1'}]}


def test_export_role_cmd_stdout(db, capsys, monkeypatch):
    import authentic2.management.commands.export_site

    monkeypatch.setattr(authentic2.management.commands.export_site, 'export_site', dummy_export_site)
    management.call_command('export_site')
    out, dummy = capsys.readouterr()
    assert json.loads(out) == dummy_export_site()


def test_export_role_cmd_to_file(db, monkeypatch, tmpdir):
    import authentic2.management.commands.export_site

    monkeypatch.setattr(authentic2.management.commands.export_site, 'export_site', dummy_export_site)
    outfile = tmpdir.join('export.json')
    management.call_command('export_site', '--output', outfile.strpath)
    with outfile.open('r') as f:
        assert json.loads(f.read()) == dummy_export_site()


def test_import_site_cmd(db, monkeypatch, json_fixture):
    management.call_command('import_site', json_fixture({'roles': []}))


def test_import_site_cmd_infos_on_stdout(db, monkeypatch, capsys, json_fixture):
    content = {
        'roles': [
            {
                'uuid': 'a' * 32,
                'slug': 'role-slug',
                'name': 'role-name',
                'ou': None,
                'service': None,
            }
        ]
    }

    management.call_command('import_site', json_fixture(content))

    out, dummy = capsys.readouterr()
    assert 'Real run' in out
    assert '1 roles created' in out
    assert '0 roles updated' in out


def test_import_site_transaction_rollback_on_error(db, monkeypatch, capsys, json_fixture):
    def exception_import_site(*args):
        Role.objects.create(slug='role-slug')
        raise Exception()

    import authentic2.management.commands.import_site

    monkeypatch.setattr(authentic2.management.commands.import_site, 'import_site', exception_import_site)

    with pytest.raises(Exception):
        management.call_command('import_site', json_fixture({'roles': []}))

    with pytest.raises(Role.DoesNotExist):
        Role.objects.get(slug='role-slug')


def test_import_site_transaction_rollback_on_dry_run(db, monkeypatch, capsys, json_fixture):
    content = {
        'roles': [
            {
                'uuid': 'a' * 32,
                'slug': 'role-slug',
                'name': 'role-name',
                'ou': None,
                'service': None,
            }
        ]
    }

    management.call_command('import_site', '--dry-run', json_fixture(content))

    with pytest.raises(Role.DoesNotExist):
        Role.objects.get(slug='role-slug')


def test_import_site_cmd_unhandled_context_option(db, monkeypatch, capsys, json_fixture):
    content = {
        'roles': [
            {
                'uuid': 'a' * 32,
                'slug': 'role-slug',
                'name': 'role-name',
                'ou': None,
                'service': None,
            }
        ]
    }

    Role.objects.create(uuid='a' * 32, slug='role-slug', name='role-name')

    with pytest.raises(ValidationError):
        management.call_command('import_site', '-o', 'role-delete-orphans', json_fixture(content))


def test_import_site_cmd_unknown_context_option(db, tmpdir, monkeypatch, capsys):
    from django.core.management.base import CommandError

    export_file = tmpdir.join('roles-export.json')
    with pytest.raises(CommandError):
        management.call_command('import_site', '-o', 'unknown-option', export_file.strpath)


@pytest.mark.skipif(VERSION >= (2, 0), reason="'stdin' command kwarg deprecated from django 2 onwards")
def test_import_site_confirm_prompt_yes(db, monkeypatch, json_fixture):
    content = {
        'roles': [
            {
                'uuid': 'a' * 32,
                'slug': 'role-slug',
                'name': 'role-name',
                'ou': None,
                'service': None,
            }
        ]
    }

    def yes_raw_input(*args, **kwargs):
        return 'yes'

    monkeypatch.setattr(__builtin__, 'input', yes_raw_input)

    management.call_command('import_site', json_fixture(content), stdin='yes')
    assert Role.objects.get(uuid='a' * 32)


def test_import_site_update_roles(db, json_fixture):
    r1 = Role.objects.create(name='Role1', slug='role1')
    Role.objects.create(name='Role2', slug='role2')

    content = {
        'roles': [
            {
                'ou': None,
                'service': None,
                'slug': r1.slug,
                'name': 'Role first update',
            }
        ]
    }

    management.call_command('import_site', json_fixture(content))

    r1.refresh_from_db()
    assert r1.name == 'Role first update'

    content['roles'][0]['uuid'] = r1.uuid
    content['roles'][0]['slug'] = 'slug-updated'
    content['roles'][0]['name'] = 'Role second update'

    management.call_command('import_site', json_fixture(content))

    r1.refresh_from_db()
    assert r1.slug == 'slug-updated'
    assert r1.name == 'Role second update'


def test_import_site_empty_uuids(db, monkeypatch, json_fixture):
    with pytest.raises(ValidationError):
        management.call_command(
            'import_site',
            json_fixture(
                {
                    'roles': [
                        {'uuid': '', 'slug': 'role-slug', 'name': 'role-name', 'ou': None, 'service': None}
                    ]
                }
            ),
        )


def test_import_site_cmd_set_absent_ou_to_default(db, json_fixture):
    minimal_json_export = {'roles': [{'name': 'first'}, {'name': 'second'}]}

    management.call_command(
        'import_site', '-o', 'set-absent-ou-to-default', json_fixture(minimal_json_export)
    )
    assert Role.objects.get(name='first', ou=get_default_ou())
    assert Role.objects.get(name='second', ou=get_default_ou())
