# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

from django.core.exceptions import ValidationError
from django.template import TemplateSyntaxError, VariableDoesNotExist, engines
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _


class TemplateError(Exception):
    pass


class Template:
    def __init__(self, value, raises=False):
        self.value = value
        self.raises = raises

        try:
            self.template = engines['django'].from_string(value)
        except TemplateSyntaxError as e:
            if self.raises:
                raise TemplateError(_('template syntax error: %s') % e)

    def render(self, context=None, request=None):
        if not hasattr(self, 'template'):
            # oops, silent error during initialization, let's get outta here
            return force_str(self.value)
        try:
            rendered = self.template.render(context=context or {}, request=request)
        except TemplateSyntaxError as e:
            if self.raises:
                raise TemplateError(_('template syntax error: %s') % e)
            return force_str(self.value)
        except VariableDoesNotExist as e:
            if self.raises:
                raise TemplateError(_('missing template variable: %s') % e)
            return force_str(self.value)
        return force_str(rendered)

    def null_render(self, context=None):
        return str(self.value)


def validate_template(value):
    try:
        Template(value, raises=True)
    except TemplateError as e:
        raise ValidationError('%s' % e)


def validate_condition_template(value):
    validate_template('{%% if %s %%}OK{%% endif %%}' % value)


def evaluate_condition_template(value, context):
    template = Template('{%% if %s %%}OK{%% endif %%}' % value)
    return template.render(context) == 'OK'
