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

import hashlib
from collections import OrderedDict

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext as _


class LockedFieldFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__lock_fields()

    def __lock_fields(self):
        # Locked fields are modified to use a read-only TextInput
        # widget remapped to a name which will be ignored by Form
        # implementation
        locked_fields = {}
        for name in self.fields:
            if not self.is_field_locked(name):
                continue
            field = self.fields[name]
            initial = self.initial[name]
            try:
                choices = field.choices
            except AttributeError:
                # BooleanField case
                if isinstance(initial, bool):
                    initial = _('Yes') if initial else _('No')
                else:
                    # Most other fields case
                    try:
                        initial = field.widget.format_value(initial)
                    except AttributeError:
                        # Django 1.8
                        try:
                            initial = field.widget._format_value(initial)
                        except AttributeError:
                            pass
            else:
                for key, label in choices:
                    if initial == key:
                        initial = label
                        break
            locked_fields[name] = forms.CharField(
                label=field.label,
                help_text=field.help_text,
                initial=initial,
                required=False,
                widget=forms.TextInput(attrs={'readonly': ''}),
            )
        if not locked_fields:
            return

        new_fields = OrderedDict()
        for name in self.fields:
            if name in locked_fields:
                new_fields[name + '@disabled'] = locked_fields[name]
            else:
                new_fields[name] = self.fields[name]
        self.fields = new_fields

    def is_field_locked(self, name):
        raise NotImplementedError


class SlugMixin(forms.ModelForm):
    def save(self, commit=True):
        instance = self.instance
        if not instance.slug:
            instance.slug = slugify(str(instance.name)).lstrip('_') or instance.__class__.__name__.lower()
            qs = instance.__class__.objects.all()
            if getattr(instance, 'ou', None) is not None:
                qs = qs.filter(ou=instance.ou)
            if instance.pk:
                qs = qs.exclude(pk=instance.pk)
            new_slug = instance.slug
            i = 1
            while qs.filter(slug=new_slug).exists():
                new_slug = '%s-%d' % (instance.slug, i)
                i += 1
            instance.slug = new_slug
        if len(instance.slug) > 256:
            instance.slug = instance.slug[:252] + hashlib.md5(instance.slug.encode()).hexdigest()[:4]
        return super().save(commit=commit)
