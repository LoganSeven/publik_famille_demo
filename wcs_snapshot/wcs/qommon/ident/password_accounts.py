# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import hashlib

from django.contrib.auth.hashers import check_password, make_password
from django.utils.encoding import force_bytes
from quixote import get_publisher

from wcs.qommon import force_str

from ..storage import StorableObject

HASHING_ALGOS = {
    # by default delegate to django password handling
    'django': None,
    # legacy hashing algorithms, to check ancient passwords
    'sha': hashlib.sha1,
    'md5': hashlib.md5,
    'sha256': hashlib.sha256,
}


class PasswordAccount(StorableObject):
    _names = 'passwordaccounts'

    id = None  # id is username
    password = None
    hashing_algo = 'django'  # delegate

    awaiting_confirmation = False
    disabled = False

    user_id = None

    @classmethod
    def get_with_credentials(cls, username, password):
        account = cls.get(username)
        if not account.is_password_ok(password):
            raise KeyError()
        return get_publisher().user_class.get(account.user_id)

    def get_user(self):
        try:
            return get_publisher().user_class.get(self.user_id)
        except KeyError:
            return None

    user = property(get_user)

    @classmethod
    def get_by_user_id(cls, user_id):
        for account in cls.select():
            if str(account.user_id) == str(user_id):
                return account
        raise KeyError()

    def is_password_ok(self, password):
        if self.hashing_algo is None:
            return self.password == password
        if self.hashing_algo == 'django':
            return check_password(password, self.password)
        return self.password == force_str(
            HASHING_ALGOS.get(self.hashing_algo)(force_bytes(password)).hexdigest()
        )

    def set_password(self, password):
        if self.hashing_algo is None:
            self.password = password
        elif self.hashing_algo == 'django':
            self.password = make_password(password)
        else:
            self.password = force_str(HASHING_ALGOS.get(self.hashing_algo)(force_bytes(password)).hexdigest())
