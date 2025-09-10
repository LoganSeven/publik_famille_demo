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


from quixote import get_publisher
from quixote.html import htmltext

from wcs.qommon import _
from wcs.qommon.form import CheckboxesWidget, ConditionWidget, HtmlWidget, StringWidget
from wcs.qommon.misc import get_dependencies_from_template

from .base import CssClassesWidget, Field, register_field_class


class TitleField(Field):
    key = 'title'
    description = _('Title')
    html_tag = 'h3'
    display_locations = ['validation', 'summary']
    section = 'display'
    is_no_data_field = True

    def add_to_form(self, form, value=None):
        import wcs.workflows

        extra_attributes = ' data-field-id="%s"' % self.id
        if self.extra_css_class:
            extra_attributes += ' class="%s"' % self.extra_css_class
        title_markup = '<{html_tag}{extra_attributes}>%s</{html_tag}>'.format(
            html_tag=self.html_tag,
            extra_attributes=extra_attributes,
        )
        label = wcs.workflows.template_on_formdata(
            None, get_publisher().translate(self.label), autoescape=False
        )
        widget = HtmlWidget(htmltext(title_markup) % label)
        widget.field = self
        form.widgets.append(widget)
        return widget

    add_to_view_form = add_to_form

    def fill_admin_form(self, form, formdef):
        form.add(StringWidget, 'label', title=_('Label'), value=self.label, required=True, size=50)
        form.add(
            CssClassesWidget,
            'extra_css_class',
            title=_('Extra classes for CSS styling'),
            value=self.extra_css_class,
            size=30,
            tab=('display', _('Display')),
        )
        form.add(
            ConditionWidget,
            'condition',
            title=_('Display Condition'),
            value=self.condition,
            required=False,
            size=50,
            tab=('display', _('Display')),
        )
        form.add(
            CheckboxesWidget,
            'display_locations',
            title=_('Display Locations'),
            options=self.get_display_locations_options(),
            value=self.display_locations,
            default_value=self.__class__.display_locations,
            tab=('display', _('Display')),
        )

    def get_admin_attributes(self):
        return Field.get_admin_attributes(self) + ['extra_css_class', 'display_locations']

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield from get_dependencies_from_template(self.label)


register_field_class(TitleField)
