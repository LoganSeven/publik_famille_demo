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

from datetime import date

import pytest

from authentic2.a2_rbac.models import Role
from authentic2.custom_user.models import DeletedUser, User
from authentic2.models import Attribute


def test_roles_and_parents(db):
    rchild1 = Role.objects.create(name='role-child1')
    rparent1 = Role.objects.create(name='role-parent1')
    rparent2 = Role.objects.create(name='role-parent2')
    rchild2 = Role.objects.create(name='role-child2')
    rparent1.add_child(rchild1)
    rparent1.add_child(rchild2)
    rparent2.add_child(rchild1)
    rparent2.add_child(rchild2)

    user1 = User.objects.create(username='user')
    user1.roles.add(rchild1)
    assert {r.id for r in user1.roles_and_parents()} == {rchild1.id, rparent1.id, rparent2.id}
    for r in user1.roles_and_parents():
        if r.id == rchild1.id:
            assert r.member == [user1]
        else:
            assert r.id in [rparent1.id, rparent2.id]
            assert r.member == []
    user1.roles.remove(rchild1)
    user1.roles.add(rchild2)
    assert {r.id for r in user1.roles_and_parents()} == {rchild2.id, rparent1.id, rparent2.id}
    for r in user1.roles_and_parents():
        if r.id == rchild2.id:
            assert r.member == [user1]
        else:
            assert r.id in [rparent1.id, rparent2.id]
            assert r.member == []


def test_user_delete(db):
    user = User.objects.create(username='foo', email='foo@example.net')
    user_id = user.id
    user_uuid = user.uuid
    user.delete()

    User.objects.create(username='foo2', email='foo@example.net')
    user = User.objects.get()
    assert user.id != user_id
    assert user.username == 'foo2'

    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_user_id == user_id
    assert deleted_user.old_uuid == user_uuid
    assert deleted_user.old_email == 'foo@example.net'


def test_user_full_name_identifiers_fallback(db, phone_activated_authn):
    user = User.objects.create(
        username='foo',
        email='foo@example.net',
        first_name='Foo',
        last_name='Bar',
    )
    user.attributes.phone = '+33611223344'
    user.save()

    assert user.get_full_name() == 'Foo Bar'

    # no last name, only first name is displayed
    user.last_name = ''
    user.save()
    assert user.get_full_name() == 'Foo'

    # if first and last names are absent, fallback on username
    user.first_name = ''
    user.save()
    assert user.get_full_name() == 'foo'

    # if username is absent, fallback on email
    user.username = ''
    user.save()
    assert user.get_full_name() == 'foo@example.net'

    # if email is absent, fallback on phone identifier
    user.email = ''
    user.save()
    assert user.get_full_name() == '+33611223344'


@pytest.fixture
def fts(db):
    Attribute.objects.create(name='adresse', label='adresse', searchable=True, kind='string')
    Attribute.objects.create(name='telephone', label='telephone', searchable=True, kind='phone_number')
    Attribute.objects.create(name='dob', label='dob', searchable=True, kind='birthdate')
    user1 = User.objects.create(
        username='foo1234', first_name='Jo', last_name='darmettein', email='jean.darmette@example.net'
    )
    user2 = User.objects.create(
        username='bar1234', first_name='Lea', last_name='darmettein', email='micheline.darmette@example.net'
    )
    user3 = User.objects.create(
        first_name='',
        last_name='peuplier',
    )
    user1.attributes.adresse = '4 rue des peupliers 13001 MARSEILLE'
    user2.attributes.adresse = '4 rue des peupliers 13001 MARSEILLE'
    user1.attributes.telephone = '0601020304'
    user2.attributes.telephone = '0601020305'
    user1.attributes.dob = date(1970, 1, 1)
    user2.attributes.dob = date(1972, 2, 2)
    return locals()


def test_fts_uuid(fts):
    assert User.objects.free_text_search(fts['user1'].uuid).count() == 1
    assert User.objects.free_text_search(fts['user2'].uuid).count() == 1


def test_fts_phone(fts):
    assert list(User.objects.free_text_search('0601020304')) == [fts['user1']]
    assert list(User.objects.free_text_search('0601020305')) == [fts['user2']]


def test_fts_dob(fts):
    assert User.objects.free_text_search('01/01/1970').count() == 1
    assert User.objects.free_text_search('02/02/1972').count() == 1
    assert User.objects.free_text_search('03/03/1973').count() == 0


def test_fts_email(fts):
    assert User.objects.free_text_search('jean.darmette@example.net').count() == 1
    assert (
        User.objects.free_text_search('jean.damrette@example.net').count() == 2
    )  # no exact match, lookup by trigrams
    assert User.objects.free_text_search('micheline.darmette@example.net').count() == 1
    assert User.objects.free_text_search('@example.net').count() == 2
    assert User.objects.free_text_search('@example').count() == 2


def test_fts_username(fts):
    assert User.objects.free_text_search('foo1234').count() == 1
    assert User.objects.free_text_search('bar1234').count() == 1


def test_fts_trigram(fts):
    assert User.objects.free_text_search('darmettein').count() == 2
    # dist attribute signals queryset from find_duplicates()
    assert hasattr(User.objects.free_text_search('darmettein')[0], 'dist')

    assert list(
        User.objects.free_text_search('lea darmettein')
        .filter(dist=0.0)
        .values_list('last_name', 'first_name')
    ) == [('darmettein', 'Lea')]
    assert hasattr(User.objects.free_text_search('darmettein')[0], 'dist')


def test_fts_last_name(db):
    first_names = [
        'Albert',
        'Michel',
        'Nicole',
        'Sylviane',
        'Jean-Pierre',
        'JEAN PIERRE',
        'Jean-Claude',
        'Jeanine',
    ]
    for first_name in first_names:
        User.objects.create(last_name='ROSSET', first_name=first_name)
    User.objects.create(last_name='RUSSO', first_name='Rossetta')
    assert list(
        User.objects.free_text_search('rosset')
        .filter(dist__lt=0.2)
        .values_list('last_name', 'first_name', 'dist')
    ) == [
        ('ROSSET', 'Albert', 0.0),
        ('ROSSET', 'Jean-Claude', 0.0),
        ('ROSSET', 'Jeanine', 0.0),
        ('ROSSET', 'Jean-Pierre', 0.0),
        ('ROSSET', 'JEAN PIERRE', 0.0),
        ('ROSSET', 'Michel', 0.0),
        ('ROSSET', 'Nicole', 0.0),
        ('ROSSET', 'Sylviane', 0.0),
    ]


def test_fts_legacy(fts):
    assert User.objects.free_text_search('rue des peupliers').count() == 3


def test_fts_legacy_and_trigram(fts):
    assert User.objects.free_text_search('peuplier').count() == 3


def test_profile_type_model(db):
    from authentic2.custom_user.models import ProfileType

    pft = ProfileType.objects.create(name='a' * 62)
    assert pft.slug == 'a' * 62
    pft = ProfileType.objects.create(name='a' * 62)
    assert pft.slug == 'a' * 62 + '-1'

    pft = ProfileType.objects.create(name='a' * 63)
    assert pft.slug == 'a' * 63
    pft = ProfileType.objects.create(name='a' * 63)
    assert pft.slug == 'a' * 30 + '-1-' + 'a' * 31


def test_service_profile_type(db):
    from authentic2.custom_user.models import ProfileType, ServiceProfileType
    from authentic2.models import Service

    service = Service.objects.create(name='aaa')
    pft = ProfileType.objects.create(name='bbb')
    ServiceProfileType.objects.create(service=service, profile_type=pft)
    assert list(service.profile_types.all()) == [pft]
    assert list(pft.services.all()) == [service]


def test_user_email_verified(app, simple_user, superuser_or_admin):
    simple_user.set_email_verified(True, source='tests')
    simple_user.save()
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert user.email_verified_sources == ['tests']

    simple_user.set_email_verified(True, source='other')
    simple_user.save()
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert user.email_verified_sources == ['tests', 'other']

    simple_user.set_email_verified(False, source='tests')
    simple_user.save()
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert user.email_verified_sources == ['other']

    simple_user.set_email_verified(True, source='other')
    simple_user.save()
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert user.email_verified_sources == ['other']

    simple_user.set_email_verified(False, source='other')
    simple_user.save()
    user = User.objects.get(id=simple_user.id)
    assert not user.email_verified
    assert user.email_verified_sources == []
