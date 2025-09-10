# w.c.s. - web application for online forms
# Copyright (C) 2005-2018  Entr'ouvert
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

import django.template.smartif
from django.template import Context, Template, TemplateSyntaxError
from django.utils.encoding import force_str
from quixote import get_publisher

from .qommon import _


class ValidationError(ValueError):
    pass


class Condition:
    record_errors = True

    def __init__(self, condition, context=None, record_errors=True):
        if not condition:
            condition = {}
        self.type = condition.get('type')
        self.value = condition.get('value')
        self.context = context or {}
        self.record_errors = record_errors

    def __repr__(self):
        return '<%s (%s) %r>' % (self.__class__.__name__, self.type, self.value)

    def get_data(self):
        return get_publisher().substitutions.get_context_variables(mode='%s-condition' % self.type)

    def unsafe_evaluate(self):
        if not self.type or not self.value:
            return True
        local_variables = self.get_data()
        return getattr(self, 'evaluate_' + self.type)(local_variables)

    def evaluate(self, source_label=None, source_url=None):
        with get_publisher().error_context(
            condition=self.value, condition_type=self.type, source_label=source_label, source_url=source_url
        ):
            try:
                return self.unsafe_evaluate()
            except Exception as e:
                if self.record_errors:
                    summary = _('Failed to evaluate condition')
                    get_publisher().record_error(
                        summary,
                        formdata=self.context.get('formdata'),
                        status_item=self.context.get('status_item'),
                        exception=e,
                    )
                raise RuntimeError()

    def evaluate_django(self, local_variables):
        template = Template('{%% if %s %%}OK{%% endif %%}' % self.value)
        context = Context(local_variables)
        return template.render(context) == 'OK'

    def validate(self):
        if not self.type or not self.value:
            return
        if not hasattr(self, 'validate_' + self.type):
            raise ValidationError(_('unknown condition type'))
        return getattr(self, 'validate_' + self.type)()

    def validate_django(self):
        try:
            Template('{%% if %s %%}OK{%% endif %%}' % self.value)
        except (TemplateSyntaxError, OverflowError) as e:
            raise ValidationError(_('syntax error: %s') % force_str(force_str(e)))

    def is_always_false(self):
        if self.type == 'django' and isinstance(self.value, str):
            try:
                self.validate()
            except ValidationError:
                # expression is not valid, it will always return False in item.check_condition()
                return True
            cleaned = self.value.strip().lower()
            if cleaned in ['false', '0']:
                return True
            if cleaned.split() == ['true', '==', 'false']:
                return True
            if cleaned.split() == ['false', '==', 'true']:
                return True
        return False


# add support for "in" and "not in" operators with left operand being a lazy
# value.
def lazy_eval(context, x):
    x = x.eval(context)
    if hasattr(x, 'get_value'):
        x = x.get_value()
    return x


django.template.smartif.OPERATORS['in'] = django.template.smartif.infix(
    django.template.smartif.OPERATORS['in'].lbp,
    lambda context, x, y: lazy_eval(context, x) in y.eval(context),
)

django.template.smartif.OPERATORS['not in'] = django.template.smartif.infix(
    django.template.smartif.OPERATORS['not in'].lbp,
    lambda context, x, y: lazy_eval(context, x) not in y.eval(context),
)
