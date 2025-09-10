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

import datetime
import random
import string

from django.utils.timezone import make_aware, now

from .storage import Equal, Less, StorableObject


class Token(StorableObject):
    _names = 'tokens'
    MAX_DELAY = 365

    type = None
    expiration = None
    context = None

    def __init__(self, expiration_delay=None, size=16, chars=None, id=None):
        super().__init__(id or self.get_new_id(size=size, chars=chars))
        if expiration_delay:
            self.set_expiration_delay(expiration_delay or 86400)

    def set_expiration_delay(self, expiration_delay):
        expiration_delay = min(expiration_delay, self.MAX_DELAY * 86400)  # max 1 year.
        self.expiration = now() + datetime.timedelta(seconds=expiration_delay)

    @classmethod
    def get_new_id(cls, create=False, size=16, chars=None):
        chars = chars or list(string.digits + string.ascii_letters)
        r = random.SystemRandom()
        while True:
            id = ''.join([r.choice(chars) for x in range(size)])
            if not cls.has_key(id):
                return id

    @classmethod
    def get_or_create(cls, type, id=None, context=None, expiration_delay=None):
        try:
            clauses = [Equal('type', type)]
            if id is not None:
                clauses.append(Equal('id', id))
            elif context is not None:
                clauses.append(Equal('context', context))
            return (cls.select(clause=clauses)[0], False)
        except (IndexError, KeyError):
            token = cls(id=id, expiration_delay=expiration_delay)
            token.id = id
            token.type = type
            token.context = context
            token.store()
            return (token, True)

    def migrate(self):
        if isinstance(self.expiration, (float, int)):
            self.expiration = min(
                self.expiration, (now() + datetime.timedelta(days=self.MAX_DELAY)).timestamp()
            )
            self.expiration = make_aware(datetime.datetime.fromtimestamp(self.expiration), is_dst=True)
        self.expiration_check()

    def expiration_check(self):
        if self.expiration and self.expiration < now():
            self.remove_self()
            raise KeyError()

    @classmethod
    def clean(cls):
        # noqa pylint: disable=unexpected-keyword-arg
        cls.wipe(clause=[Less('expiration', now())])
