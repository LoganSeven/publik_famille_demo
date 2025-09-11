# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) 2022 Entr'ouvert
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

from authentic2.custom_user.models import User
from authentic2_auth_fc.models import FcAccount


def test_password_change_view_with_fc(app, db):
    user = User.objects.create(username='jdoe')
    app.set_user('jdoe')

    response = app.get('/accounts/password/change/')
    assert len(response.pyquery('.messages')) == 0
    assert User.objects.count() == 1

    FcAccount.objects.create(sub='1234', user=user)
    response = app.get('/accounts/password/change/')
    assert 'FranceConnect' in response.pyquery('.messages .warning').text()
