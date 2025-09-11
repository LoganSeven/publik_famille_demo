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

from django import forms
from django.db.models import BooleanField


class UniqueBooleanField(BooleanField):
    """BooleanField allowing only one True value in the table, and preventing
    problems with multiple False values by implicitely converting them to
    None."""

    def __init__(self, *args, **kwargs):
        kwargs['unique'] = True
        kwargs['blank'] = True
        kwargs['null'] = True
        kwargs['default'] = False
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        del kwargs['null']
        del kwargs['blank']
        del kwargs['unique']
        del kwargs['default']
        return name, path, args, kwargs

    def to_python(self, value):
        value = super().to_python(value)
        if value is None:
            return False
        return value

    def get_db_prep_value(self, value, connection, prepared=False):
        value = super().get_db_prep_value(value, connection, prepared=prepared)
        if value is False:
            return None
        return value

    def formfield(self, **kwargs):
        # Unlike most fields, BooleanField figures out include_blank from
        # self.null instead of self.blank.
        if self.choices:
            include_blank = False
            defaults = {'choices': self.get_choices(include_blank=include_blank)}
        else:
            defaults = {'form_class': forms.BooleanField}
        defaults.update(kwargs)
        return super().formfield(**defaults)
