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

from . import widgets


class Select2Mixin:
    def __init__(self, **kwargs):
        kwargs['queryset'] = self.widget.get_initial_queryset()
        super().__init__(**kwargs)

    def __setattr__(self, key, value):
        if key == 'queryset':
            self.widget.queryset = value
        super().__setattr__(key, value)


class Select2ModelChoiceField(Select2Mixin, forms.ModelChoiceField):
    pass


class Select2ModelMultipleChoiceField(Select2Mixin, forms.ModelMultipleChoiceField):
    pass


for key in dir(widgets):
    cls = getattr(widgets, key)
    if not isinstance(cls, type):
        continue
    if issubclass(cls, widgets.ModelSelect2MultipleWidget):
        cls_name = key.replace('Widget', 'Field')
        vars()[cls_name] = type(
            cls_name,
            (Select2ModelMultipleChoiceField,),
            {
                'widget': cls,
            },
        )
    elif issubclass(cls, widgets.ModelSelect2Widget):
        cls_name = key.replace('Widget', 'Field')
        vars()[cls_name] = type(
            cls_name,
            (Select2ModelChoiceField,),
            {
                'widget': cls,
            },
        )
