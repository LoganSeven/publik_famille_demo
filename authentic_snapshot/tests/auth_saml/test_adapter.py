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

import re
from unittest import mock

import lasso
import pytest
from mellon.adapters import UserCreationError

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.custom_user.models import User
from authentic2_auth_saml.adapters import MappingError
from authentic2_auth_saml.models import AddRoleAction, SAMLAuthenticator, SetAttributeAction


def test_get_idps(adapter, idp):
    assert len(list(adapter.get_idps())) == 1
    other = SAMLAuthenticator.objects.create(slug='idp2', enabled=False)
    assert len(list(adapter.get_idps())) == 1
    other.enabled = True
    other.save()
    assert len(list(adapter.get_idps())) == 1


def test_lookup_user_ok(adapter, idp, saml_attributes, title_attribute):
    assert User.objects.count() == 0

    user = adapter.lookup_user(idp, saml_attributes)
    user.refresh_from_db()
    assert user.email == 'john.doe@example.com'
    assert user.attributes.title == 'Mr.'
    assert user.first_name == 'John'
    assert user.attributes.title == 'Mr.'
    assert user.ou.default is True

    assert user.can_change_email()
    SAMLAuthenticator.objects.all().update(allow_user_change_email=False)
    assert not user.can_change_email()


def test_create_user_in_authenticator_ou(adapter, idp, saml_attributes, title_attribute):
    assert User.objects.count() == 0
    # fallback on default ou when authenticator has no OU
    user = adapter.lookup_user(idp, saml_attributes)
    assert user.ou.default is True

    # don't change user's OU on authenticator's OU change
    new_ou = OrganizationalUnit.objects.create(name='Test')
    idp['authenticator'].ou = new_ou
    idp['authenticator'].save()

    user = adapter.lookup_user(idp, saml_attributes)
    user.refresh_from_db()
    assert user.ou.default is True
    assert User.objects.count() == 1

    new_attributes = saml_attributes.copy()
    new_attributes.update({'mail': ['foo@example.com'], 'name_id_content': 'yyy'})

    new_user = adapter.lookup_user(idp, new_attributes)
    assert User.objects.count() == 2

    assert new_user.ou == new_ou
    assert not new_user.ou.default


def test_lookup_user_missing_mandatory_attribute(adapter, idp, saml_attributes, title_attribute):
    del saml_attributes['mail']

    assert User.objects.count() == 0
    assert adapter.lookup_user(idp, saml_attributes) is None
    assert User.objects.count() == 0


def test_apply_attribute_mapping_missing_attribute_logged(
    caplog, adapter, idp, saml_attributes, title_attribute, user
):
    caplog.set_level('WARNING')
    saml_attributes['http://nice/attribute/givenName'] = []
    adapter.provision_a2_attributes(user, idp, saml_attributes)
    assert re.match('.*no value.*first_name', caplog.records[-1].message)


class TestAddRole:
    @pytest.fixture
    def idp(self, simple_role, role_random, role_ou1):
        authenticator = SAMLAuthenticator.objects.create(
            enabled=True,
            metadata='meta1.xml',
            slug='idp1',
        )
        AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)
        AddRoleAction.objects.create(
            authenticator=authenticator, role=role_random, condition='"Test" in attributes.groups'
        )
        return authenticator.settings

    @pytest.fixture
    def saml_attributes(self):
        return {
            'issuer': 'https://idp.com/',
            'name_id_content': 'xxx',
            'name_id_format': lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
            'groups': ['Test'],
        }

    def test_lookup_user_success(self, adapter, simple_role, role_random, role_ou1, idp, saml_attributes):
        user = adapter.lookup_user(idp, saml_attributes)
        assert simple_role in user.roles.all()
        assert role_random in user.roles.all()
        assert role_ou1 not in user.roles.all()

        saml_attributes['groups'] = 'New group'
        adapter.action_add_role(user, idp, saml_attributes)
        assert role_random not in user.roles.all()


def test_addroleaction_migration(role_random, migration):
    SAMLAuthenticator.objects.create(
        enabled=True,
        metadata='meta1.xml',
        slug='idp1',
    )

    old_apps = migration.before([('authenticators', '0016_alter_addroleaction_condition')])
    AddRoleAction = old_apps.get_model('authenticators', 'AddRoleAction')
    BaseAuthenticator = old_apps.get_model('authenticators', 'BaseAuthenticator')
    Role = old_apps.get_model('a2_rbac', 'Role')
    base_authenticator = BaseAuthenticator.objects.get(slug='idp1')
    role = Role.objects.get(slug=role_random.slug)
    ara = AddRoleAction.objects.create(
        authenticator=base_authenticator, role=role, attribute_name='groups', attribute_value='Test'
    )
    ara_nocondition = AddRoleAction.objects.create(authenticator=base_authenticator, role=role)

    new_apps = migration.apply([('authenticators', '0017_auto_20230927_1517')])  # buggy migration
    AddRoleAction = new_apps.get_model('authenticators', 'AddRoleAction')
    ara = AddRoleAction.objects.get(id=ara.id)
    ara_nocondition = AddRoleAction.objects.get(id=ara_nocondition.id)
    assert ara.condition == 'attributes.groups in "Test"'
    assert ara_nocondition.condition == 'attributes. in ""'  # buggy condition

    new_apps = migration.apply([('authenticators', '0019_fix_addroleaction_condition')])
    AddRoleAction = new_apps.get_model('authenticators', 'AddRoleAction')
    ara = AddRoleAction.objects.get(id=ara.id)
    ara_nocondition = AddRoleAction.objects.get(id=ara_nocondition.id)
    assert ara.condition == 'attributes.groups in "Test"'
    assert ara_nocondition.condition == ''  # fixed


def test_apply_attribute_mapping_missing_attribute_exception(
    adapter, idp, saml_attributes, title_attribute, user, rf
):
    saml_attributes['http://nice/attribute/givenName'] = []
    SetAttributeAction.objects.filter(user_field='first_name').update(mandatory=True)
    with pytest.raises(MappingError, match='no value'):
        adapter.provision_a2_attributes(user, idp, saml_attributes)

    request = rf.get('/')
    request._messages = mock.Mock()
    adapter.request = request
    with pytest.raises(UserCreationError):
        adapter.finish_create_user(idp, saml_attributes, user)
    request._messages.add.assert_called_once_with(
        40, 'User creation failed: no value for attribute "first_name".', ''
    )
