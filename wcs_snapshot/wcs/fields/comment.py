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


import re

from quixote import get_publisher
from quixote.html import htmlescape

from wcs.qommon import _
from wcs.qommon.form import (
    CheckboxesWidget,
    CommentWidget,
    ComputedExpressionWidget,
    ConditionWidget,
    TextWidget,
    WysiwygTextWidget,
)
from wcs.qommon.misc import get_dependencies_from_template

from .base import CssClassesWidget, Field, register_field_class


class CommentField(Field):
    key = 'comment'
    description = _('Comment')
    display_locations = []
    section = 'display'
    is_no_data_field = True

    def get_text(self):
        import wcs.workflows

        label = self.get_html_content()
        return wcs.workflows.template_on_html_string(label)

    def add_to_form(self, form, value=None):
        widget = CommentWidget(content=self.get_text(), extra_css_class=self.extra_css_class)
        form.widgets.append(widget)
        widget.field = self
        return widget

    def add_to_view_form(self, *args, **kwargs):
        if self.include_in_validation_page:
            return self.add_to_form(*args, **kwargs)
        return None

    def get_html_content(self):
        if not self.label:
            return ''
        label = get_publisher().translate(self.label)
        if label.startswith('<'):
            return label
        if '\n\n' in label:
            # blank lines to paragraphs
            label = '</p>\n<p>'.join([str(htmlescape(x)) for x in re.split('\n\n+', label)])
            return '<p>' + label + '</p>'
        return '<p>%s</p>' % str(htmlescape(label))

    def fill_admin_form(self, form, formdef):
        if self.label and (self.label[0] != '<' and '[end]' in self.label):
            form.add(
                TextWidget,
                'label',
                title=_('Label'),
                value=self.label,
                validation_function=ComputedExpressionWidget.validate_template,
                required=True,
                cols=70,
                rows=3,
                render_br=False,
            )
        else:
            form.add(
                WysiwygTextWidget,
                'label',
                title=_('Label'),
                validation_function=ComputedExpressionWidget.validate_template,
                value=self.get_html_content(),
                required=True,
            )
        form.add(
            CssClassesWidget,
            'extra_css_class',
            title=_('Extra classes for CSS styling'),
            value=self.extra_css_class,
            size=30,
            advanced=True,
        )
        form.add(
            ConditionWidget,
            'condition',
            title=_('Display Condition'),
            value=self.condition,
            required=False,
            size=50,
            advanced=True,
        )
        form.add(
            CheckboxesWidget,
            'display_locations',
            title=_('Display Locations'),
            options=self.get_display_locations_options(),
            value=self.display_locations,
            advanced=True,
        )

    def get_admin_attributes(self):
        return Field.get_admin_attributes(self) + ['extra_css_class', 'display_locations']

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield from get_dependencies_from_template(self.label)


register_field_class(CommentField)
