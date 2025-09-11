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

import io
import re
import warnings

import phonenumbers
import PIL.Image
from django import forms
from django.conf import settings
from django.core import validators
from django.core.files import File
from django.forms import CharField, EmailField, FileField, ModelChoiceField, MultiValueField, ValidationError
from django.forms.fields import FILE_INPUT_CONTRADICTION
from django.utils.translation import gettext_lazy as _

from authentic2 import app_settings
from authentic2.a2_rbac.models import Role
from authentic2.forms.widgets import (
    CheckPasswordInput,
    EmailInput,
    NewPasswordInput,
    PasswordInput,
    PhoneWidget,
    ProfileImageInput,
)
from authentic2.manager.utils import label_from_role
from authentic2.validators import email_validator


class PasswordField(CharField):
    widget = PasswordInput


class NewPasswordField(CharField):
    widget = NewPasswordInput

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_strength = None

    def _get_min_strength(self):
        return self._min_strength

    def _set_min_strength(self, value):
        self._min_strength = value
        self.widget.min_strength = value

    min_strength = property(_get_min_strength, _set_min_strength)


class CheckPasswordField(CharField):
    widget = CheckPasswordInput

    def __init__(self, *args, **kwargs):
        kwargs['help_text'] = (
            '''
    <span class="a2-password-check-equality-default">%(default)s</span>
    <span class="a2-password-check-equality-matched">%(match)s</span>
    <span class="a2-password-check-equality-unmatched">%(nomatch)s</span>
'''
            % {
                'default': _('Both passwords must match.'),
                'match': _('Passwords match.'),
                'nomatch': _('Passwords do not match.'),
            }
        )
        super().__init__(*args, **kwargs)


class ProfileImageField(FileField):
    widget = ProfileImageInput

    @property
    def image_size(self):
        return app_settings.A2_ATTRIBUTE_KIND_PROFILE_IMAGE_SIZE

    def clean(self, data, initial=None):
        if data is FILE_INPUT_CONTRADICTION or data is False or data is None:
            return super().clean(data, initial=initial)
        # we have a file
        try:
            with warnings.catch_warnings():
                image = PIL.Image.open(io.BytesIO(data.read()))
        except (OSError, PIL.Image.DecompressionBombWarning, ValueError):
            # ValueError is raised by PngImagePlugin when
            # "Decompressed Data Too Large" (#95153)
            raise ValidationError(_('The image is not valid'))
        image = self.normalize_image(image)
        new_data = self.file_from_image(image, data.name)
        return super().clean(new_data, initial=initial)

    def file_from_image(self, image, name=None):
        output = io.BytesIO()
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(output, format='JPEG', quality=99, optimize=1)
        output.seek(0)
        return File(output, name=name)

    def normalize_image(self, image):
        width = height = self.image_size
        if abs((1.0 * width / height) - (1.0 * image.size[0] / image.size[1])) > 0.1:
            # aspect ratio change, crop the image first
            box = [0, 0, image.size[0], int(image.size[0] * (1.0 * height / width))]

            if box[2] > image.size[0]:
                box = [int(t * (1.0 * image.size[0] / box[2])) for t in box]
            if box[3] > image.size[1]:
                box = [int(t * (1.0 * image.size[1] / box[3])) for t in box]

            if image.size[0] > image.size[1]:  # landscape
                box[0] = (image.size[0] - box[2]) / 2  # keep the middle
                box[2] += box[0]
            else:
                box[1] = (image.size[1] - box[3]) / 4  # keep mostly the top
                box[3] += box[1]

            image = image.crop(box)
        try:
            resampling_algorithm = PIL.Image.Resampling.LANCZOS
        except AttributeError:
            # can be removed when Pillow < 9.1.0 is not supported anymore
            resampling_algorithm = PIL.Image.LANCZOS
        return image.resize([width, height], resampling_algorithm)


class ValidatedEmailField(EmailField):
    default_validators = [email_validator]
    widget = EmailInput

    def __init__(self, *args, max_length=254, **kwargs):
        error_messages = kwargs.pop('error_messages', {})
        if 'invalid' not in error_messages:
            error_messages['invalid'] = _(
                'Please enter a valid email address (example: john.doe@entrouvert.com)'
            )
        super().__init__(*args, max_length=max_length, error_messages=error_messages, **kwargs)


class RoleChoiceField(ModelChoiceField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queryset = Role.objects.exclude(slug__startswith='_')

    def label_from_instance(self, obj):
        return label_from_role(obj)


class ListValidator:
    def __init__(self, item_validator):
        self.item_validator = item_validator

    def __call__(self, value):
        for i, item in enumerate(value):
            try:
                self.item_validator(item)
            except ValidationError as e:
                raise ValidationError(_('Item {0} is invalid: {1}').format(i, e.args[0]))


class CommaSeparatedInput(forms.TextInput):
    def format_value(self, value):
        if not value:
            return ''
        if not isinstance(value, str):
            return ', '.join(value)
        return value


class CommaSeparatedCharField(forms.Field):
    widget = CommaSeparatedInput

    def __init__(self, dedup=True, max_length=None, min_length=None, *args, **kwargs):
        self.dedup = dedup
        self.max_length = max_length
        self.min_length = min_length
        item_validators = kwargs.pop('item_validators', [])
        super().__init__(*args, **kwargs)
        for item_validator in item_validators:
            self.validators.append(ListValidator(item_validator))

    def to_python(self, value):
        if value in validators.EMPTY_VALUES:
            return []

        value = [item.strip() for item in value.split(',') if item.strip()]
        if self.dedup:
            value = list(set(value))

        return value

    def clean(self, value):
        value = self.to_python(value)
        self.validate(value)
        self.run_validators(value)
        return value


class PhoneField(MultiValueField):
    widget = PhoneWidget

    def __init__(self, **kwargs):
        fields = (
            CharField(max_length=8, initial=settings.DEFAULT_COUNTRY_CODE),
            CharField(max_length=16, required=False),
        )
        kwargs['help_text'] = _(
            'Please select an international prefix and input your local number (the '
            'leading zero “0” for some local numbers may be removed or left as is).'
        )
        super().__init__(error_messages=None, fields=fields, require_all_fields=False, **kwargs)

    def compress(self, data_list):
        if data_list and data_list[1]:
            country_code = data_list[0]
            data_list[0] = '+%s' % data_list[0]
            data_list[1] = re.sub(r'[-.\s/]', '', data_list[1])

            conf = []
            for conf_key in (
                'region',
                'region_desc',
                'example_value',
            ):
                conf.append(
                    settings.PHONE_COUNTRY_CODES.get(country_code, {}).get(conf_key, None)
                    or settings.PHONE_COUNTRY_CODES[settings.DEFAULT_COUNTRY_CODE][conf_key]
                )
            if all(conf[1:]):
                validation_error_message = _(
                    'Invalid phone number. Phone number from {location} must respect local format (e.g. {example}).'
                ).format(location=conf[1], example=conf[2])
            else:
                # missing human-friendly config elements, can't provide a clearer validation error message:
                validation_error_message = _('Invalid phone number.')
            try:
                pn = phonenumbers.parse(''.join(data_list), conf[0])
            except phonenumbers.NumberParseException:
                raise ValidationError(validation_error_message)
            if not phonenumbers.is_valid_number(pn):
                raise ValidationError(validation_error_message)
            return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
        return ''
