# authentic2-auth-fc - authentic2 authentication for FranceConnect
# Copyright (C) 2019 Entr'ouvert
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

from authentic2_auth_fc.models import FcAccount


def test_api_fc_unlink(app, admin, simple_user):
    FcAccount.objects.create(user=simple_user)
    url = '/api/users/%s/fc-unlink/' % simple_user.uuid
    # test unauthorized caller
    app.delete(url, status=401)
    # test unauthorized method
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    app.get(url, status=405)
    # test success
    app.delete(url, status=204)
    assert FcAccount.objects.filter(user=simple_user).exists() is False


def test_api_user_franceconnect(settings, app, admin, simple_user, franceconnect):
    FcAccount.objects.create(user=simple_user, sub='1234')

    url = '/api/users/%s/' % simple_user.uuid
    # test unauthorized method
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    response = app.get(url)
    assert 'franceconnect' not in response.json
    response = app.get(url + '?full')
    assert 'franceconnect' in response.json, 'missing franceconnect field in user API'
    content = response.json['franceconnect']
    assert isinstance(content, dict), 'franceconnect field is not a dict'
    assert content.get('linked') is True
    assert content.get('link_url').startswith('https://')
    assert content.get('link_url').endswith('/callback/')
    assert content.get('unlink_url').startswith('https://')
    assert content.get('unlink_url').endswith('/unlink/')

    unlink_url = '/api/users/%s/fc-unlink/' % simple_user.uuid
    app.delete(unlink_url, status=204)

    response = app.get(url + '?full')
    assert response.json['franceconnect']['linked'] is False
