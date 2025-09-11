# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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
from django.utils.text import slugify

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.utils import get_default_ou

OU_SERIALIZATION_FIELDS = [
    'check_required_on_login_attributes',
    'clean_unused_accounts_alert',
    'clean_unused_accounts_deletion',
    'colour',
    'default',
    'description',
    'email_is_unique',
    'home_url',
    'id',
    'logo',
    'name',
    'show_username',
    'slug',
    'user_add_password_policy',
    'user_can_reset_password',
    'username_is_unique',
    'uuid',
    'validate_emails',
    'phone_is_unique',
]


def test_unauthorized(app):
    app.get('/api/ous/', status=401)


class TestAuthenticated:
    @pytest.fixture
    def app(self, app, admin):
        app.authorization = ('Basic', ('admin', admin.clear_password))
        return app

    def test_get_by_uuid(self, app):
        resp = app.get(f'/api/ous/{get_default_ou().uuid}/')
        assert set(resp.json) == set(OU_SERIALIZATION_FIELDS)
        assert resp.json['uuid'] == get_default_ou().uuid

    def test_get_by_slug(self, app):
        resp = app.get('/api/ous/default/')
        assert set(resp.json) == set(OU_SERIALIZATION_FIELDS)
        assert resp.json['uuid'] == get_default_ou().uuid

    def test_post_name_is_required(self, app):
        # no slug no name
        ou_data = {
            'id': 42,
        }
        assert OU.objects.all().count() == 1
        resp = app.post_json('/api/ous/', params=ou_data, status=400)
        assert OU.objects.all().count() == 1
        assert resp.json == {'errors': {'name': ['This field is required.']}, 'result': 0}

    def test_post_name_and_slug_not_unique(self, app):
        OU.objects.create(name='Some Organizational Unit')

        # another call with same ou name
        ou_data = {
            'name': 'Some Organizational Unit',
        }
        assert OU.objects.all().count() == 2
        resp = app.post_json('/api/ous/', params=ou_data, status=400)
        assert OU.objects.all().count() == 2
        assert resp.json == {
            'errors': {
                '__all__': [
                    'The fields name must make a unique set.',
                    'The fields slug must make a unique set.',
                ]
            },
            'result': 0,
        }

    def test_post_no_slug(self, app):
        ou_data = {
            'name': 'Some Organizational Unit',
        }
        assert OU.objects.all().count() == 1
        resp = app.post_json('/api/ous/', params=ou_data)
        assert OU.objects.all().count() == 2
        uuid = resp.json['uuid']
        ou = OU.objects.get(uuid=uuid)
        assert ou.id != get_default_ou().id
        assert ou.slug == slugify(ou.name)
        assert ou.slug == slugify(ou_data['name'])

    def test_get_or_create(self, app):
        # first get-or-create? -> create
        ou_data = {
            'name': 'Some Organizational Unit',
        }
        slug = 'some-organizational-unit'
        resp = app.post_json('/api/ous/', params=ou_data)
        assert resp.json['slug'] == slug
        uuid = resp.json['uuid']

        ou_data = {
            'name': 'Another name',
            'slug': slug,
        }
        resp = app.post_json('/api/ous/?get_or_create=slug', params=ou_data)
        assert resp.json['uuid'] == uuid
        assert resp.json['name'] == 'Some Organizational Unit'

        # update-or-create? -> update
        ou_data = {
            'name': 'Another name',
            'slug': slug,
        }
        resp = app.post_json('/api/ous/?update_or_create=slug', params=ou_data)
        assert resp.json['uuid'] == uuid
        assert resp.json['name'] == 'Another name'
        ou = OU.objects.get(uuid=uuid)
        assert ou.name == 'Another name'
        assert ou.slug == slug
