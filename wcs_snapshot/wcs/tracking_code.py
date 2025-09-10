# w.c.s. - web application for online forms
# Copyright (C) 2005-2015  Entr'ouvert
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

import random

import wcs.sql


class TrackingCode(wcs.sql.TrackingCode):
    _names = 'tracking-codes'
    id = None
    formdef_id = None
    formdata_id = None

    CHARS = 'BCDFGHJKLMNPQRSTVWXZ'
    SIZE = 8

    def __init__(self):
        # do not call to StorableObject.__init__ as we don't want to have
        # self.id set at this point.
        pass

    @classmethod
    def get(cls, id, **kwargs):
        return super().get(id.upper(), **kwargs)

    @classmethod
    def get_new_id(cls, create=False):
        r = random.SystemRandom()
        return ''.join([r.choice(cls.CHARS) for x in range(cls.SIZE)])

    def store(self):
        if self.id is None:
            while True:
                self.id = self.get_new_id()
                if not self.has_key(self.id):
                    break
        super().store()

    @property
    def formdef(self):
        from wcs.formdef import FormDef

        return FormDef.get(self.formdef_id)

    @formdef.setter
    def formdef(self, value):
        self.formdef_id = str(value.id)

    @property
    def formdata(self):
        return self.formdef.data_class().get(self.formdata_id)

    @formdata.setter
    def formdata(self, value):
        self.formdef = value.formdef
        self.formdata_id = str(value.id)
        self.store()
        value.tracking_code = self.id
        value.store()
