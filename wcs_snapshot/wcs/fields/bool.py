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

from quixote import get_request
from quixote.html import TemplateIO, htmltext

from wcs.qommon import N_, _
from wcs.qommon.form import CheckboxWidget
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import WidgetField, register_field_class


class BoolField(WidgetField):
    key = 'bool'
    description = _('Check Box (single choice)')
    allow_complex = True
    allow_statistics = True
    available_for_filter = True

    widget_class = CheckboxWidget
    required = 'optional'
    anonymise = 'no'

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        if not edit:
            kwargs['disabled'] = 'disabled'
            value = get_request().get_field(self.field_key)
            form.add_hidden(self.field_key, value=str(value or False))
            widget = form.get_widget(self.field_key)
            widget.field = self
            if value and not value == 'False':
                self.field_key = 'f%sdisabled' % self.id
                get_request().form[self.field_key] = 'yes'
            self.field_key = 'f%sdisabled' % self.id

    def get_view_value(self, value, **kwargs):
        if value is True or value == 'True':
            return str(_('Yes'))
        if value is False or value == 'False':
            return str(_('No'))
        return ''

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        span = ET.Element('{%s}span' % OD_NS['text'])
        span.text = od_clean_text(self.get_view_value(value))
        return span

    def convert_value_from_anything(self, value):
        if isinstance(value, str):
            return self.convert_value_from_str(value)
        return bool(value)

    def convert_value_from_str(self, value):
        if value is None:
            return None
        for true_word in (N_('True'), N_('Yes')):
            if str(value).lower() in (true_word.lower(), _(true_word).lower()):
                return True
        return False

    def convert_value_to_str(self, value):
        if value is True:
            return 'True'
        if value is False:
            return 'False'
        return value

    def stats(self, values):
        no_records = len(values)
        if not no_records:
            return
        r = TemplateIO(html=True)
        r += htmltext('<table class="stats">')
        r += htmltext('<thead><tr><th colspan="4">')
        r += self.label
        r += htmltext('</th></tr></thead>')
        options = (True, False)
        r += htmltext('<tbody>')
        for o in options:
            r += htmltext('<tr>')
            r += htmltext('<td class="label">')
            if o is True:
                r += str(_('Yes'))
                value = True
            else:
                r += str(_('No'))
                value = False
            r += htmltext('</td>')
            no = len([None for x in values if self.convert_value_from_str(x.data.get(self.id)) is value])

            r += htmltext('<td class="percent">')
            r += htmltext(' %.2f&nbsp;%%') % (100.0 * no / no_records)
            r += htmltext('</td>')
            r += htmltext('<td class="total">')
            r += '(%d/%d)' % (no, no_records)
            r += htmltext('</td>')
            r += htmltext('</tr>')
            r += htmltext('<tr>')
            r += htmltext('<td class="bar" colspan="3">')
            r += htmltext('<span style="width: %d%%"></span>' % (100 * no / no_records))
            r += htmltext('</td>')
            r += htmltext('</tr>')
        r += htmltext('</tbody>')
        r += htmltext('</table>')
        return r.getvalue()

    def from_json_value(self, value):
        if value is None:
            return value
        return bool(value)


register_field_class(BoolField)
