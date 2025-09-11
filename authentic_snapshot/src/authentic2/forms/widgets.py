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

# Bootstrap django-datetime-widget is a simple and clean widget for DateField,
# Timefiled and DateTimeField in Django framework. It is based on Bootstrap
# datetime picker, supports Bootstrap 2
#
# https://github.com/asaglimbeni/django-datetime-widget
#
# License: BSD
# Initial Author: Alfredo Saglimbeni

import datetime
import json
import re
import uuid

import phonenumbers
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.forms.widgets import ClearableFileInput, DateInput, DateTimeInput
from django.forms.widgets import EmailInput as BaseEmailInput
from django.forms.widgets import MultiWidget
from django.forms.widgets import PasswordInput as BasePasswordInput
from django.forms.widgets import TextInput, TimeInput
from django.utils.encoding import force_str
from django.utils.formats import get_format, get_language
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from gadjo.templatetags.gadjo import xstatic

from authentic2 import app_settings
from authentic2.models import Attribute
from authentic2.passwords import get_password_checker

DATE_FORMAT_JS_PY_MAPPING = {
    'P': '%p',
    'ss': '%S',
    'ii': '%M',
    'hh': '%H',
    'HH': '%I',
    'dd': '%d',
    'mm': '%m',
    'yy': '%y',
    'yyyy': '%Y',
}

DATE_FORMAT_TO_PYTHON_REGEX = re.compile(r'\b(' + '|'.join(DATE_FORMAT_JS_PY_MAPPING.keys()) + r')\b')


DATE_FORMAT_PY_JS_MAPPING = {
    '%M': 'ii',
    '%m': 'mm',
    '%I': 'HH',
    '%H': 'hh',
    '%d': 'dd',
    '%Y': 'yyyy',
    '%y': 'yy',
    '%p': 'P',
    '%S': 'ss',
}

DATE_FORMAT_TO_JS_REGEX = re.compile(r'(?<!\w)(' + '|'.join(DATE_FORMAT_PY_JS_MAPPING.keys()) + r')\b')


BOOTSTRAP_INPUT_TEMPLATE = """
      %(rendered_widget)s
      %(clear_button)s
      <span class="add-on"><i class="icon-th"></i></span>
      <span class="helptext">%(help_text)s</span>
       <script type="text/javascript">
           $("#%(id)s").datetimepicker({%(options)s});
       </script>
       """

BOOTSTRAP_DATE_INPUT_TEMPLATE = """
      %(rendered_widget)s
      %(clear_button)s
      <span class="add-on"><i class="icon-th"></i></span>
      <span class="%(id)s helptext">%(help_text)s</span>
       <script type="text/javascript">
           if ($("#%(id)s").attr('type') != "date") {
               $("#%(id)s").datetimepicker({%(options)s});
               var date = new Date($("#%(id)s").val());
               $("#%(id)s").val(date.toLocaleDateString('%(language)s'));
           } else {
               $(".%(id)s.helptext").hide();
           }
       </script>
       """

CLEAR_BTN_TEMPLATE = """<span class="add-on"><i class="icon-remove"></i></span>"""


class PickerWidgetMixin:
    class Media:
        css = {
            'all': ('css/datetimepicker.css',),
        }
        js = (
            xstatic('jquery', 'jquery.min.js'),
            xstatic('jquery_ui', 'jquery-ui.min.js'),
            'js/bootstrap-datetimepicker.js',
            'js/locales/bootstrap-datetimepicker.fr.js',
        )

    format_name = None
    glyphicon = None
    help_text = None

    render_template = BOOTSTRAP_INPUT_TEMPLATE

    def __init__(self, attrs=None, options=None, usel10n=None):
        if attrs is None:
            attrs = {}

        self.options = options
        self.options['language'] = get_language().split('-')[0]

        # We're not doing localisation, get the Javascript date format provided by the user,
        # with a default, and convert it to a Python data format for later string parsing
        date_format = self.options['format']
        self.format = DATE_FORMAT_TO_PYTHON_REGEX.sub(
            lambda x: DATE_FORMAT_JS_PY_MAPPING[x.group()], date_format
        )

        super().__init__(attrs, format=self.format)

    def get_format(self):
        format = get_format(self.format_name)[0]
        for py, js in DATE_FORMAT_PY_JS_MAPPING.items():
            format = format.replace(py, js)
        return format

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        final_attrs = self.build_attrs(attrs)
        final_attrs['class'] = 'controls input-append date'
        rendered_widget = super().render(name, value, attrs=final_attrs, renderer=renderer)

        # if not set, autoclose have to be true.
        self.options.setdefault('autoclose', True)

        # Build javascript options out of python dictionary
        options_list = []
        for key, value in iter(self.options.items()):
            options_list.append('%s: %s' % (key, json.dumps(value)))

        js_options = ',\n'.join(options_list)

        # Use provided id or generate hex to avoid collisions in document
        id = final_attrs.get('id', uuid.uuid4().hex)

        help_text = self.help_text
        if not help_text:
            help_text = '%s %s' % (_('Format:'), self.options['format'])

        return mark_safe(
            self.render_template
            % dict(
                id=id,
                rendered_widget=rendered_widget,
                clear_button=CLEAR_BTN_TEMPLATE if self.options.get('clearBtn') else '',
                glyphicon=self.glyphicon,
                language=get_language(),
                options=js_options,
                help_text=help_text,
            )
        )


class DateTimeWidget(PickerWidgetMixin, DateTimeInput):
    """
    DateTimeWidget is the corresponding widget for Datetime field, it renders both the date and time
    sections of the datetime picker.
    """

    format_name = 'DATETIME_INPUT_FORMATS'
    glyphicon = 'glyphicon-th'

    def __init__(self, attrs=None, options=None, usel10n=None):
        if options is None:
            options = {}

        # Set the default options to show only the datepicker object
        options['format'] = options.get('format', self.get_format())

        super().__init__(attrs, options, usel10n)


class DateWidget(PickerWidgetMixin, DateInput):
    """
    DateWidget is the corresponding widget for Date field, it renders only the date section of
    datetime picker.
    """

    format_name = 'DATE_INPUT_FORMATS'
    glyphicon = 'glyphicon-calendar'
    input_type = 'date'
    render_template = BOOTSTRAP_DATE_INPUT_TEMPLATE

    def __init__(self, attrs=None, options=None, usel10n=None):
        if options is None:
            options = {}

        # Set the default options to show only the datepicker object
        options['startView'] = options.get('startView', 2)
        options['minView'] = options.get('minView', 2)
        options['format'] = options.get('format', self.get_format())

        super().__init__(attrs, options, usel10n)

    def format_value(self, value):
        if value is not None:
            if isinstance(value, datetime.datetime):
                return force_str(value.isoformat())
            return value


class TimeWidget(PickerWidgetMixin, TimeInput):
    """
    TimeWidget is the corresponding widget for Time field, it renders only the time section of
    datetime picker.
    """

    format_name = 'TIME_INPUT_FORMATS'
    glyphicon = 'glyphicon-time'

    def __init__(self, attrs=None, options=None, usel10n=None):
        if options is None:
            options = {}

        # Set the default options to show only the timepicker object
        options['startView'] = options.get('startView', 1)
        options['minView'] = options.get('minView', 0)
        options['maxView'] = options.get('maxView', 1)
        options['format'] = options.get('format', self.get_format())

        super().__init__(attrs, options, usel10n)


class PasswordInput(BasePasswordInput):
    class Media:
        js = (
            xstatic('jquery', 'jquery.min.js'),
            'authentic2/js/password.js',
        )
        css = {'all': ('authentic2/css/password.css',)}

    def render(self, name, value, attrs=None, renderer=None):
        if attrs is None:
            attrs = {}
        if 'autocomplete' not in attrs:
            attrs['autocomplete'] = 'current-password'
        output = super().render(name, value, attrs=attrs, renderer=renderer)
        if attrs and app_settings.A2_PASSWORD_POLICY_SHOW_LAST_CHAR:
            _id = attrs.get('id')
            if _id:
                output += '''\n<script>a2_password_show_last_char(%s);</script>''' % json.dumps(_id)
        return output


class NewPasswordInput(PasswordInput):
    template_name = 'authentic2/widgets/new_password.html'
    min_strength = None

    def get_context(self, *args, **kwargs):
        context = super().get_context(*args, **kwargs)
        password_checker = get_password_checker()
        checks = list(password_checker(''))
        context['checks'] = checks
        return context

    def render(self, name, value, attrs=None, renderer=None):
        if attrs is None:
            attrs = {}
        attrs['autocomplete'] = 'new-password'

        if self.min_strength is not None:
            attrs['data-min-strength'] = self.min_strength

        output = super().render(name, value, attrs=attrs, renderer=renderer)
        if attrs:
            _id = attrs.get('id')
            if _id:
                output += '''\n<script>a2_password_validate(%s);</script>''' % json.dumps(_id)
        return output


class CheckPasswordInput(PasswordInput):
    # this widget must be named xxx2 and the other widget xxx1, it's a
    # convention, js code expect it.
    def render(self, name, value, attrs=None, renderer=None):
        if attrs is None:
            attrs = {}
        attrs['autocomplete'] = 'new-password'
        output = super().render(name, value, attrs=attrs, renderer=renderer)
        if attrs:
            _id = attrs.get('id')
            if _id and _id.endswith('2'):
                other_id = _id[:-1] + '1'
                output += '''\n<script>$(a2_password_check_equality(%s, %s))</script>''' % (
                    json.dumps(other_id),
                    json.dumps(_id),
                )
        return output


class ProfileImageInput(ClearableFileInput):
    template_name = 'authentic2/profile_image_input.html'

    def __init__(self, *args, **kwargs):
        attrs = kwargs.pop('attrs', {})
        attrs['accept'] = 'image/*'
        super().__init__(*args, attrs=attrs, **kwargs)


class DatalistTextInput(TextInput):
    def __init__(self, name='', data=(), attrs=None):
        super().__init__(attrs)
        self.data = data
        self.name = 'list__%s' % name
        self.attrs.update({'list': self.name})

    def render(self, name, value, attrs=None, renderer=None):
        output = super().render(name, value, attrs=attrs, renderer=renderer)
        datalist = '<datalist id="%s">' % self.name
        for element in self.data:
            datalist += '<option value="%s">' % element
        datalist += '</datalist>'
        output += datalist
        return output


class PhoneNumberInput(TextInput):
    input_type = 'tel'


class EmailInput(BaseEmailInput):
    template_name = 'authentic2/widgets/email.html'

    @property
    def media(self):
        if app_settings.A2_SUGGESTED_EMAIL_DOMAINS:
            return forms.Media(
                js=(
                    xstatic('jquery', 'jquery.min.js'),
                    'authentic2/js/email_domains_suggestions.js',
                )
            )

    def get_context(self, *args, **kwargs):
        context = super().get_context(*args, **kwargs)
        if app_settings.A2_SUGGESTED_EMAIL_DOMAINS:
            context['widget']['attrs']['data-suggested-domains'] = ':'.join(
                app_settings.A2_SUGGESTED_EMAIL_DOMAINS
            )
            context['domains_suggested'] = True
        return context


class SelectAttributeWidget(forms.Select):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = [('', '---------')] + list(self.get_options().items())

    @staticmethod
    def get_options():
        choices = {}
        for name in ('email', 'username', 'first_name', 'last_name'):
            field = get_user_model()._meta.get_field(name)
            choices[name] = '%s (%s)' % (capfirst(field.verbose_name), name)
        for attribute in Attribute.objects.exclude(name__in=choices):
            choices[attribute.name] = '%s (%s)' % (attribute.label, attribute.name)
        choices['ou__slug'] = _('Organizational unit slug (ou__slug)')
        return choices


class PhoneWidget(MultiWidget):
    class Media:
        css = {'all': ('authentic2/css/style.css',)}

    def __init__(self, attrs=None):
        prefixes = ((code, f'+{code}') for code in settings.PHONE_COUNTRY_CODES.keys())
        widgets = [
            forms.Select(attrs=attrs, choices=prefixes),
            forms.TextInput(attrs=attrs),
        ]
        super().__init__(widgets, attrs)

    def decompress(self, value):
        try:
            # phone number stored in E.164 international format, no country specifier needed here
            pn = phonenumbers.parse(value)
        except phonenumbers.NumberParseException:
            pass
        else:
            if pn:
                code = str(pn.country_code)
                # retrieve the string representation from pn.national_number integer
                raw_number = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL)
                return [code, raw_number]
        return [settings.DEFAULT_COUNTRY_CODE, value]


class HiddenPhoneInput(forms.HiddenInput, PhoneWidget):
    pass
