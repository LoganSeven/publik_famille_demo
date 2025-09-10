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

import xml.etree.ElementTree as ET

from quixote.html import htmltext

from wcs.qommon import _
from wcs.qommon.form import EmailWidget
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import WidgetField, register_field_class


class EmailField(WidgetField):
    key = 'email'
    description = _('Email')
    use_live_server_validation = True
    available_for_filter = True

    widget_class = EmailWidget

    def convert_value_from_str(self, value):
        return value

    def get_view_value(self, value, **kwargs):
        return htmltext('<a href="mailto:%s">%s</a>') % (value, value)

    def get_rst_view_value(self, value, indent=''):
        return indent + value

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        a = ET.Element('{%s}a' % OD_NS['text'])
        a.text = od_clean_text(value)
        a.attrib['{%s}href' % OD_NS['xlink']] = 'mailto:' + a.text
        return a


register_field_class(EmailField)
