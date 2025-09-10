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
from wcs.qommon.form import CheckboxesWidget, CheckboxWidget, IntWidget, PasswordEntryWidget, StringWidget

from .base import WidgetField, register_field_class


class PasswordField(WidgetField):
    key = 'password'
    description = _('Password')

    min_length = 0
    max_length = 0
    count_uppercase = 0
    count_lowercase = 0
    count_digit = 0
    count_special = 0
    confirmation = True
    confirmation_title = None
    strength_indicator = True
    formats = ['sha1']
    extra_attributes = [
        'formats',
        'min_length',
        'max_length',
        'count_uppercase',
        'count_lowercase',
        'count_digit',
        'count_special',
        'confirmation',
        'confirmation_title',
        'strength_indicator',
    ]

    widget_class = PasswordEntryWidget

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + self.extra_attributes

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        formats = [
            ('cleartext', _('Clear text')),
            ('md5', _('MD5')),
            ('sha1', _('SHA1')),
        ]
        form.add(
            CheckboxesWidget,
            'formats',
            title=_('Storage formats'),
            value=self.formats,
            options=formats,
            inline=True,
        )
        form.add(IntWidget, 'min_length', title=_('Minimum length'), value=self.min_length)
        form.add(
            IntWidget,
            'max_length',
            title=_('Maximum password length'),
            value=self.max_length,
            hint=_('0 for unlimited length'),
        )
        form.add(
            IntWidget,
            'count_uppercase',
            title=_('Minimum number of uppercase characters'),
            value=self.count_uppercase,
        )
        form.add(
            IntWidget,
            'count_lowercase',
            title=_('Minimum number of lowercase characters'),
            value=self.count_lowercase,
        )
        form.add(IntWidget, 'count_digit', title=_('Minimum number of digits'), value=self.count_digit)
        form.add(
            IntWidget,
            'count_special',
            title=_('Minimum number of special characters'),
            value=self.count_special,
        )
        form.add(
            CheckboxWidget,
            'strength_indicator',
            title=_('Add a password strength indicator'),
            value=self.strength_indicator,
            default_value=self.__class__.strength_indicator,
        )
        form.add(
            CheckboxWidget,
            'confirmation',
            title=_('Add a confirmation input'),
            value=self.confirmation,
            default_value=self.__class__.confirmation,
        )
        form.add(
            StringWidget,
            'confirmation_title',
            size=50,
            title=_('Label for confirmation input'),
            value=self.confirmation_title,
        )

    def get_view_value(self, value, **kwargs):
        return '●' * 8

    def get_csv_value(self, element, **kwargs):
        return [self.get_view_value(element)]

    def get_rst_view_value(self, value, indent=''):
        return indent + self.get_view_value(value)


register_field_class(PasswordField)
