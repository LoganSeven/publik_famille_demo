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
import pytest
from django.contrib.contenttypes.models import ContentType

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role


def test_natural_key_json(db, ou1):
    role = Role.objects.create(slug='role1', name='Role1', ou=ou1)

    for ou in OU.objects.all():
        nk = ou.natural_key_json()
        assert nk == {'uuid': ou.uuid, 'slug': ou.slug, 'name': ou.name}

        assert ou == OU.objects.get_by_natural_key_json(nk)

    for ct in ContentType.objects.all():
        nk = ct.natural_key_json()
        assert nk == {'app_label': ct.app_label, 'model': ct.model}
        assert ct == ContentType.objects.get_by_natural_key_json(nk)

    # test is not useful if there are no FK set
    assert Role.objects.filter(ou__isnull=False).exists()

    for role in Role.objects.all():
        nk = role.natural_key_json()
        ou_nk = role.ou and role.ou.natural_key_json()
        service_nk = role.service and role.service.natural_key_json()
        assert nk == {
            'uuid': role.uuid,
            'slug': role.slug,
            'name': role.name,
            'ou': ou_nk,
            'service': service_nk,
        }
        assert role == Role.objects.get_by_natural_key_json(nk)
        assert role == Role.objects.get_by_natural_key_json({'uuid': role.uuid})
        if service_nk:
            with pytest.raises(Role.DoesNotExist):
                Role.objects.get_by_natural_key_json({'slug': role.slug, 'ou': ou_nk})
        else:
            assert Role.objects.get_by_natural_key_json({'slug': role.slug, 'ou': ou_nk}) == role
        if service_nk:
            with pytest.raises(Role.DoesNotExist):
                assert Role.objects.get_by_natural_key_json({'name': role.name, 'ou': ou_nk})
        else:
            assert Role.objects.get_by_natural_key_json({'name': role.name, 'ou': ou_nk}) == role
        assert role == Role.objects.get_by_natural_key_json(
            {'slug': role.slug, 'ou': ou_nk, 'service': service_nk}
        )
        assert role == Role.objects.get_by_natural_key_json(
            {'name': role.name, 'ou': ou_nk, 'service': service_nk}
        )

    for permission in Permission.objects.all():
        ou_nk = permission.ou and permission.ou.natural_key_json()
        target_ct_nk = permission.target_ct.natural_key_json()
        target_nk = permission.target.natural_key_json()
        op_nk = permission.operation.natural_key_json()

        nk = permission.natural_key_json()
        assert nk == {
            'operation': op_nk,
            'ou': ou_nk,
            'target_ct': target_ct_nk,
            'target': target_nk,
        }
        assert permission == Permission.objects.get_by_natural_key_json(nk)
