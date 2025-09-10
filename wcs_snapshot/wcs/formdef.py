# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

from wcs.formdef_base import FormDefBase
from wcs.qommon.storage import StorableObject
from wcs.sql import SqlFormDef


class FormDef(FormDefBase, SqlFormDef):
    def storage_store(self, **kwargs):
        SqlFormDef.store(self, **kwargs)


class FileFormDef(FormDefBase, StorableObject):
    # legacy class for migration
    _names = 'formdefs'
    _reset_class = False

    def storage_store(self, comment=None, *args, **kwargs):
        StorableObject.store(self, **kwargs)
