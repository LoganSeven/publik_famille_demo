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

import base64

try:
    import cPickle as pickle
except ImportError:
    import pickle

from django import forms
from django.contrib.humanize.templatetags.humanize import apnumber
from django.core.exceptions import ValidationError
from django.db import models
from django.template.defaultfilters import pluralize
from django.utils.encoding import force_bytes, force_str
from django.utils.text import capfirst


def loads(value):
    # value is always an unicode string
    value = force_bytes(value)
    value = base64.b64decode(value)
    return pickle.loads(value)


def dumps(value):
    return PickledObject(force_str(base64.b64encode(pickle.dumps(value, protocol=0))))


# This is a copy of http://djangosnippets.org/snippets/513/
#
# A field which can store any pickleable object in the database. It is
# database-agnostic, and should work with any database backend you can throw at
# it.
#
# Pass in any object and it will be automagically converted behind the scenes,
# and you never have to manually pickle or unpickle anything. Also works fine
# when querying.
#
# Initial author: Oliver Beattie


class PickledObject(str):
    """A subclass of string so it can be told whether a string is
    a pickled object or not (if the object is an instance of this class
    then it must [well, should] be a pickled one)."""


class PickledObjectField(models.Field):
    def __from_db_value(self, value):
        # Reading value from db
        if value is not None:
            value = loads(value)
        return value

    def from_db_value(self, value, expression, connection):
        return self.__from_db_value(value)

    def get_prep_value(self, value):
        # Preparing value for db
        if value is not None and not isinstance(value, PickledObject):
            value = dumps(value)
        return value

    def get_internal_type(self):
        return 'TextField'

    def get_lookup(self, lookup_name):
        """
        No lookup is possible.
        """
        raise TypeError('Lookup type %s is not supported.' % lookup_name)


# This is a modified copy of http://djangosnippets.org/snippets/1200/
#
# We added a validate method.
#
# Usually you want to store multiple choices as a manytomany link to another
# table. Sometimes however it is useful to store them in the model itself. This
# field implements a model field and an accompanying formfield to store multiple
# choices as a comma-separated list of values, using the normal CHOICES
# attribute.
#
# You'll need to set maxlength long enough to cope with the maximum number of
# choices, plus a comma for each.
#
# The normal get_FOO_display() method returns a comma-delimited string of the
# expanded values of the selected choices.
#
# The formfield takes an optional max_choices parameter to validate a maximum
# number of choices.
#
# Initial author: Daniel Roseman


class MultiSelectFormField(forms.MultipleChoiceField):
    widget = forms.CheckboxSelectMultiple

    def __init__(self, *args, **kwargs):
        self.max_choices = kwargs.pop('max_choices', 0)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        if not value and self.required:
            raise forms.ValidationError(self.error_messages['required'])
        if value and self.max_choices and len(value) > self.max_choices:
            raise forms.ValidationError(
                'You must select a maximum of %s choice%s.'
                % (apnumber(self.max_choices), pluralize(self.max_choices))
            )
        return value


class MultiSelectField(models.Field):
    def get_internal_type(self):
        return 'CharField'

    def get_choices_default(self):
        return self.get_choices(include_blank=False)

    def _get_FIELD_display(self, field):
        pass

    def formfield(self, **kwargs):
        # don't call super, as that overrides default widget if it has choices
        defaults = {
            'required': not self.blank,
            'label': capfirst(self.verbose_name),
            'help_text': self.help_text,
            'choices': self.choices,
        }
        if self.has_default():
            defaults['initial'] = self.get_default()
        defaults.update(kwargs)
        return MultiSelectFormField(**defaults)

    def get_db_prep_value(self, value, connection, prepared=False):
        if isinstance(value, str):
            return value
        elif isinstance(value, list):
            return ','.join(value)

    def validate(self, value, model_instance):
        out = set()
        if self.choices:
            out |= {option_key for option_key, _ in self.choices}
        out = set(value) - out
        if out:
            raise ValidationError(self.error_messages['invalid_choice'] % ','.join(list(out)))
        if not value and not self.blank:
            raise ValidationError(self.error_messages['blank'])

    def to_python(self, value):
        if isinstance(value, list):
            return value
        if not value:
            return []
        return value.split(',')

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def contribute_to_class(self, cls, name):
        super().contribute_to_class(cls, name)
        if self.choices:

            def func(self, fieldname=name, choicedict=None):
                choicedict = choicedict or dict(self.choices)
                return ','.join([choicedict.get(value, value) for value in getattr(self, fieldname)])

            setattr(cls, 'get_%s_display' % self.name, func)
