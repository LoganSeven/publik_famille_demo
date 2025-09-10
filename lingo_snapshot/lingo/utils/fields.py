# lingo - payment and billing system
# Copyright (C) 2022-2023  Entr'ouvert
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

import ckeditor.fields
from django import forms
from django.conf import settings

import lingo.monkeypatch  # noqa pylint: disable=unused-import


class RichTextField(ckeditor.fields.RichTextField):
    def formfield(self, **kwargs):
        defaults = {
            'form_class': RichTextFormField,
            'config_name': self.config_name,
            'extra_plugins': self.extra_plugins,
            'external_plugin_resources': self.external_plugin_resources,
        }
        defaults.update(kwargs)
        return super().formfield(**defaults)


class RichTextFormField(ckeditor.fields.RichTextFormField):
    def clean(self, value):
        value = super().clean(value)
        if settings.LANGUAGE_CODE.startswith('fr-'):
            # apply some typographic rules
            value = value.replace('&laquo; ', '«\u202f')
            value = value.replace('« ', '«\u202f')
            value = value.replace(' &raquo;', '\u202f»')
            value = value.replace(' »', '\u202f»')
            value = value.replace(' :', '\u00a0:')
            value = value.replace(' ;', '\u202f;')
            value = value.replace(' !', '\u202f!')
            value = value.replace(' ?', '\u202f?')
        return value


class CategorySelect(forms.Select):
    template_name = 'lingo/widgets/categoryselectwidget.html'


class AgendasMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(self, queryset, **kwargs):
        # django init explicitly set empty_label to None:
        # super().__init__(queryset, empty_label=None, **kwargs)
        # but we want here an empty label in select options
        super(forms.ModelMultipleChoiceField, self).__init__(queryset, **kwargs)

    def label_from_instance(self, obj):
        if obj.category_slug:
            return f'({obj.category_label}) {obj}'
        return str(obj)


class AgendaSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option_dict = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        if value and value.instance.category_slug:
            option_dict['attrs']['data-category-id'] = value.instance.category_slug
        return option_dict
