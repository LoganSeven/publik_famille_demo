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

from django.utils.html import strip_tags
from quixote.html import htmltext

from wcs.qommon import _
from wcs.qommon.form import (
    HiddenWidget,
    MiniRichTextWidget,
    RadiobuttonsWidget,
    RichTextWidget,
    StringWidget,
    TextWidget,
)
from wcs.qommon.misc import ellipsize, is_ascii_digit, strip_some_tags
from wcs.qommon.ods import NS as OD_NS

from .base import WidgetField, register_field_class


class TextField(WidgetField):
    key = 'text'
    description = _('Long Text')
    available_for_filter = True

    widget_class = TextWidget
    cols = None
    rows = None
    pre = None
    display_mode = 'plain'
    maxlength = None
    extra_attributes = ['cols', 'rows', 'maxlength']
    prefill_selection_widget_kwargs = {'use_textarea': True}

    def migrate(self):
        changed = super().migrate()
        if isinstance(getattr(self, 'pre', None), bool):  # 2022-09-16
            if self.pre:
                self.display_mode = 'pre'
            else:
                self.display_mode = 'plain'
            self.pre = None
            changed = True
        return changed

    def perform_more_widget_changes(self, *args, **kwargs):
        if self.display_mode == 'basic-rich':
            self.widget_class = MiniRichTextWidget
        elif self.display_mode == 'rich':
            self.widget_class = RichTextWidget

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        if self.cols:
            form.add(
                StringWidget,
                'cols',
                title=_('Line length'),
                hint=_(
                    'Deprecated option, it is advised to use CSS classes '
                    'to size the fields in a manner compatible with all devices.'
                ),
                value=self.cols,
            )
        else:
            form.add(HiddenWidget, 'cols', value=None)
        form.add(StringWidget, 'rows', title=_('Number of rows'), value=self.rows)

        def validate_maxlength(value):
            if value and not is_ascii_digit(value):
                raise ValueError(_('The maximum number of characters must be empty or a number.'))

        form.add(
            StringWidget,
            'maxlength',
            title=_('Maximum number of characters'),
            value=self.maxlength,
            validation_function=validate_maxlength,
        )
        display_options = [
            ('basic-rich', _('Rich Text (simple: bold, italic...)')),
            ('rich', _('Rich Text (full: titles, lists...)')),
            ('plain', _('Plain Text (with automatic paragraphs on blank lines)')),
            ('pre', _('Plain Text (with linebreaks as typed)')),
        ]
        form.add(
            RadiobuttonsWidget,
            'display_mode',
            title=_('Text display'),
            value=self.display_mode,
            default_value='plain',
            options=display_options,
            advanced=True,
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'cols',
            'rows',
            'display_mode',
            'maxlength',
        ]

    def convert_value_from_str(self, value):
        return value

    def get_rst_view_value(self, value, indent=''):
        if self.display_mode in ('basic-rich', 'rich'):
            value = strip_tags(value or '')
        return '  %s' % ((value or '').replace('\n', '\n' + indent))

    def get_view_value(self, value, **kwargs):
        if self.display_mode == 'pre':
            return htmltext('<p class="plain-text-pre">') + value + htmltext('</p>')
        if self.display_mode == 'basic-rich':
            return htmltext(strip_some_tags(value, MiniRichTextWidget.ALL_TAGS))
        if self.display_mode == 'rich':
            return htmltext(strip_some_tags(value, RichTextWidget.ALL_TAGS))
        try:
            return (
                htmltext('<p>')
                + htmltext('\n').join([(x or htmltext('</p><p>')) for x in value.splitlines()])
                + htmltext('</p>')
            )
        except Exception:
            return ''

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        if self.display_mode in ('rich', 'basic-rich'):
            return
        paragraphs = []
        for paragraph in value.splitlines():
            if paragraph.strip():
                p = ET.Element('{%s}p' % OD_NS['text'])
                p.text = paragraph
                paragraphs.append(p)
        return paragraphs

    def get_view_short_value(self, value, max_len=30, **kwargs):
        if self.display_mode in ('rich', 'basic-rich'):
            return ellipsize(str(strip_tags(value)), max_len)
        return ellipsize(str(value), max_len)

    def get_json_value(self, value, **kwargs):
        if self.display_mode in ('rich', 'basic-rich'):
            return str(self.get_view_value(value))
        return value


register_field_class(TextField)
