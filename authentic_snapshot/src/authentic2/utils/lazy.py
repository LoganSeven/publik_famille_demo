# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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


from django.utils.encoding import force_str
from django.utils.functional import keep_lazy
from django.utils.text import format_lazy


def lazy_join(join, args):
    if not args:
        return ''

    fstring = '{}' + ''.join([join + '{}'] * (len(args) - 1))
    return format_lazy(fstring, *args)


@keep_lazy(str)
def lazy_label(default, func):
    """Allow using a getter for a label, with late binding.

    ex.: lazy_label(_('Default label'), lambda: app_settings.CUSTOM_LABEL)
    """
    return force_str(func() or default)
