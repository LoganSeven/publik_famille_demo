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

from authentic2.backends import is_user_authenticable
from authentic2.utils.misc import authenticate


def test_user_filters(settings, db, simple_user, user_ou1, ou1):
    assert authenticate(username=simple_user.username, password=simple_user.clear_password)
    assert is_user_authenticable(simple_user)
    assert is_user_authenticable(user_ou1)
    assert authenticate(username=user_ou1.username, password=user_ou1.clear_password)
    settings.A2_USER_FILTER = {'ou__slug': 'ou1'}
    assert not authenticate(username=simple_user.username, password=simple_user.clear_password)
    assert authenticate(username=user_ou1.username, password=user_ou1.clear_password)
    assert not is_user_authenticable(simple_user)
    assert is_user_authenticable(user_ou1)
    settings.A2_USER_EXCLUDE = {'ou__slug': 'ou1'}
    assert not authenticate(username=simple_user.username, password=simple_user.clear_password)
    assert not authenticate(username=user_ou1.username, password=user_ou1.clear_password)
    assert not is_user_authenticable(simple_user)
    assert not is_user_authenticable(user_ou1)
    settings.A2_USER_FILTER = {}
    assert authenticate(username=simple_user.username, password=simple_user.clear_password)
    assert not authenticate(username=user_ou1.username, password=user_ou1.clear_password)
    assert is_user_authenticable(simple_user)
    assert not is_user_authenticable(user_ou1)


def test_model_backend_phone_number(settings, db, simple_user, nomail_user, ou1, phone_activated_authn):
    nomail_user.attributes.phone = '+33123456789'
    nomail_user.save()
    simple_user.attributes.phone = '+33123456789'
    simple_user.save()
    assert authenticate(username='+33123456789', password=simple_user.clear_password) == simple_user
    assert is_user_authenticable(simple_user)
    assert authenticate(username='+33123456789', password=nomail_user.clear_password) == nomail_user
    assert is_user_authenticable(nomail_user)


def test_model_backend_phone_number_email(settings, db, simple_user, phone_activated_authn):
    simple_user.attributes.phone = '+33123456789'
    simple_user.save()
    # user with both phone number and username can authenticate in two different ways
    assert authenticate(username='user', password=simple_user.clear_password)
    assert authenticate(username='+33123456789', password=simple_user.clear_password)
    assert is_user_authenticable(simple_user)
