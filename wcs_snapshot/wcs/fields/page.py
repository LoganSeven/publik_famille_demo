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

import copy

from quixote import get_publisher
from quixote.html import TemplateIO, htmltext

from wcs.conditions import Condition
from wcs.qommon import _
from wcs.qommon.form import (
    CompositeWidget,
    ComputedExpressionWidget,
    ConditionWidget,
    StringWidget,
    VarnameWidget,
    WidgetListAsTable,
)
from wcs.qommon.misc import get_dependencies_from_template
from wcs.qommon.xml_storage import PostConditionsXmlMixin

from .base import Field, register_field_class


class PostConditionsRowWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}
        self.add(
            ConditionWidget,
            name='condition',
            title=_('Condition that must be met'),
            value=value.get('condition'),
            size=50,
        )
        self.add(
            ComputedExpressionWidget,
            name='error_message',
            title=_('Error message if condition is not met'),
            value=value.get('error_message'),
        )

    def _parse(self, request):
        if self.get('condition') or self.get('error_message'):
            self.value = {'condition': self.get('condition'), 'error_message': self.get('error_message')}
        else:
            self.value = None


class PostConditionsTableWidget(WidgetListAsTable):
    readonly = False

    def __init__(self, name, **kwargs):
        super().__init__(name, element_type=PostConditionsRowWidget, **kwargs)

    def parse(self, request=None):
        super().parse(request=request)
        for post_condition in self.value or []:
            if not (post_condition.get('error_message') and post_condition.get('condition')):
                self.set_error(_('Both condition and error message are required.'))
                break
        return self.value


class PageCondition(Condition):
    def get_data(self):
        dict_vars = self.context['dict_vars']
        formdef = self.context['formdef']

        # create variables with values currently being evaluated, not yet
        # available in the formdata.
        from wcs.formdata import get_dict_with_varnames

        live_data = {}
        form_live_data = {}
        if dict_vars is not None and formdef:
            live_data = get_dict_with_varnames(formdef.fields, dict_vars)
            form_live_data = {'form_' + x: y for x, y in live_data.items()}

        # 1) feed the form_var_* variables in the global substitution system,
        # they will shadow formdata context variables with their new "live"
        # value, this may be useful when evaluating data sources.
        class ConditionVars:
            def __init__(self, id_dict_var):
                # keep track of reference dictionary
                self.id_dict_var = id_dict_var

            def get_substitution_variables(self):
                return {}

            def get_static_substitution_variables(self):
                # only for backward compatibility with python evaluations
                return form_live_data

            def __eq__(self, other):
                # Assume all ConditionVars are equal when initialized with
                # the same live data dictionary; this avoids filling
                # the substitution sources with duplicates and invalidating its
                # cache.
                return self.id_dict_var == getattr(other, 'id_dict_var', None)

        if dict_vars is not None:
            # Add them only if there is a real dict_vars in context,
            # ie do nothing on first page condition
            get_publisher().substitutions.feed(ConditionVars(id(dict_vars)))

            # alter top-of-stack formdata with data from submitted form
            from wcs.formdata import FormData

            for source in reversed(get_publisher().substitutions.sources):
                if isinstance(source, FormData):
                    source.data.update(dict_vars)
                    break

        data = super().get_data()
        # 2) add live data as var_ variables for local evaluation only, for
        # backward compatibility. They are not added globally as they would
        # interfere with the var_ prefixed variables used in dynamic jsonp
        # fields. (#9786)
        data = copy.copy(data)
        data.update(live_data)
        if dict_vars is None:
            # ConditionsVars is not set when evaluating first page condition,
            # but we need to have form_var_* variables already; add them from
            # form_live_data (where all variables will have been set to None).
            data.update(form_live_data)
        return data


class PageField(Field, PostConditionsXmlMixin):
    key = 'page'
    description = _('Page')
    section = 'display'
    is_no_data_field = True

    post_conditions = None

    def fill_admin_form(self, form, formdef):
        form.add(StringWidget, 'label', title=_('Label'), value=self.label, required=True, size=50)
        form.add(
            ConditionWidget,
            'condition',
            title=_('Display Condition'),
            value=self.condition,
            required=False,
            size=50,
        )
        form.add(
            PostConditionsTableWidget,
            'post_conditions',
            title=_('Post Conditions'),
            value=self.post_conditions,
            advanced=True,
        )
        form.add(
            VarnameWidget,
            'varname',
            title=_('Identifier'),
            value=self.varname,
            size=30,
            advanced=True,
            hint=_('This is used as reference in workflow edition action.'),
        )

    def get_admin_attributes(self):
        return Field.get_admin_attributes(self) + ['post_conditions', 'varname']

    def add_to_view_form(self, *args, **kwargs):
        pass

    def get_conditions(self):
        if self.condition:
            yield self.condition
        for post_condition in self.post_conditions or []:
            yield post_condition.get('condition')

    def get_post_conditions_parameter_view_value(self, widget):
        if not self.post_conditions:
            return
        r = TemplateIO(html=True)
        r += htmltext('<ul>')
        for post_condition in self.post_conditions:
            r += htmltext('<li>%s <span class="condition-type">(%s)</span> - %s</li>') % (
                post_condition.get('condition').get('value'),
                {'django': 'Django'}.get(post_condition.get('condition').get('type')),
                post_condition.get('error_message'),
            )
        r += htmltext('</ul>')
        return r.getvalue()

    def get_dependencies(self):
        yield from super().get_dependencies()
        if getattr(self, 'post_conditions', None):
            post_conditions = self.post_conditions or []
            for post_condition in post_conditions:
                condition = post_condition.get('condition') or {}
                if condition.get('type') == 'django':
                    yield from get_dependencies_from_template(condition.get('value'))

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        yield location, None, self.label
        for post_condition in self.post_conditions or []:
            yield location, None, post_condition.get('error_message')


register_field_class(PageField)
