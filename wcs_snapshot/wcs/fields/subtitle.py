# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

from wcs.qommon import _

from .base import register_field_class
from .title import TitleField


class SubtitleField(TitleField):
    key = 'subtitle'
    description = _('Subtitle')
    html_tag = 'h4'
    section = 'display'
    is_no_data_field = True


register_field_class(SubtitleField)
