# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

import datetime

from django.utils.encoding import force_str
from quixote import get_publisher
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, misc, pgettext_lazy
from wcs.qommon.form import DateWidget, SingleSelectWidget, StringWidget
from wcs.sql_criterias import ArrayContains, Or


def is_unary_operator(op):
    return op in (
        'absent',
        'existing',
        'is_today',
        'is_tomorrow',
        'is_yesterday',
        'is_this_week',
        'is_future',
        'is_past',
        'is_today_or_future',
        'is_today_or_past',
    )


def render_filter_widget(filter_widget, operators, filter_field_operator_key, filter_field_operator):
    result = htmltext('<div class="widget operator-and-value-widget">')
    result += htmltext('<div class="title-and-operator">')
    result += filter_widget.render_title(filter_widget.get_title())
    if operators:
        result += htmltext('<div class="operator">')
        operator_widget = SingleSelectWidget(
            filter_field_operator_key,
            options=[(o[0], o[1], o[0]) for o in operators],
            value=filter_field_operator,
            render_br=False,
        )
        result += operator_widget.render_content()
        result += htmltext('</div>')
    result += htmltext('</div>')
    result += htmltext('<div class="value">')
    result += filter_widget.render_content()
    result += htmltext('</div>')
    result += htmltext('</div>')
    return result


class FilterField:
    can_include_in_listing = True
    id = None
    key = None
    label = None
    available_for_filter = False
    include_in_statistics = False
    geojson_label = None
    store_display_value = None
    store_structured_value = None

    def __init__(self, formdef):
        self.formdef = formdef
        self.varname = self.id.replace('-', '_')
        self.contextual_id = self.id
        self.contextual_varname = self.varname
        self.label = force_str(self.label)  # so it can be pickled
        self.geojson_label = force_str(self.geojson_label or self.label)
        self.filter_field_key = 'filter-%s-value' % self.contextual_id
        self.filter_field_operator_key = '%s-operator' % self.filter_field_key.replace('-value', '')
        self.filters_dict = {}

    def get_allowed_operators(self):
        from wcs.variables import LazyFormDefObjectsManager

        lazy_manager = LazyFormDefObjectsManager(formdef=self.formdef)
        return lazy_manager.get_field_allowed_operators(self) or []

    def get_view_value(self, value):
        # just here to quack like a duck
        return None

    def get_csv_heading(self):
        return [self.label]

    def get_csv_value(self, element, **kwargs):
        return [element]

    @property
    def has_relations(self):
        return bool(self.id == 'user-label')

    def get_filter_field_value(self):
        return self.filters_dict.get(self.filter_field_key)

    def get_filter_field_operator(self):
        return self.filters_dict.get(self.filter_field_operator_key) or 'eq'

    def render_filter_widget(self, widget):
        return render_filter_widget(
            widget,
            operators=self.get_allowed_operators(),
            filter_field_operator_key=self.filter_field_operator_key,
            filter_field_operator=self.get_filter_field_operator(),
        )


class RelatedField:
    is_related_field = True
    key = 'related-field'
    varname = None
    related_field = None
    can_include_in_listing = True
    available_for_filter = False

    def __init__(self, carddef, field, parent_field):
        self.carddef = carddef
        self.related_field = field
        self.parent_field = parent_field
        self.parent_field_id = parent_field.id

    @property
    def id(self):
        return '%s$%s' % (self.parent_field_id, self.related_field.id)

    @property
    def contextual_id(self):
        return self.id

    @property
    def contextual_varname(self):
        return None

    @property
    def label(self):
        return '%s - %s' % (self.parent_field.label, self.related_field.label)

    def __repr__(self):
        return '<%s (card: %r, parent: %r, related: %r)>' % (
            self.__class__.__name__,
            self.carddef,
            self.parent_field.label,
            self.related_field.label,
        )

    @property
    def store_display_value(self):
        return self.related_field.store_display_value

    @property
    def store_structured_value(self):
        return self.related_field.store_structured_value

    def get_view_value(self, value, **kwargs):
        if value is None:
            return ''
        if isinstance(value, bool):
            return _('Yes') if value else _('No')
        if isinstance(value, datetime.date):
            return misc.strftime(misc.date_format(), value)
        return value

    def get_view_short_value(self, value, max_len=30, **kwargs):
        return self.get_view_value(value)

    def get_csv_heading(self):
        if self.related_field:
            return self.related_field.get_csv_heading()
        return [self.label]

    def get_csv_value(self, value, **kwargs):
        if self.related_field:
            return self.related_field.get_csv_value(value, **kwargs)
        return [self.get_view_value(value)]

    def get_column_field_id(self):
        from wcs.sql import get_field_id

        return get_field_id(self.related_field)


class UserRelatedField(RelatedField):
    # it is named 'user-label' and not 'user' for compatibility with existing
    # listings, as the 'classic' user column is named 'user-label'.
    key = 'user-related-field'
    parent_field_id = 'user-label'
    store_display_value = None
    store_structured_value = None

    def __init__(self, field):
        self.related_field = field

    def __repr__(self):
        return '<%s (field: %r)>' % (
            self.__class__.__name__,
            self.related_field.label,
        )

    @property
    def label(self):
        return _('%s of User') % self.related_field.label


class UserLabelRelatedField(UserRelatedField):
    # custom user-label column, targetting the "name" (= full name) column
    # of the users table
    id = 'user-label'
    key = 'user-label'
    varname = 'user_label'
    has_relations = True

    def __init__(self):
        pass

    def __repr__(self):
        return '<UserLabelRelatedField>'

    def get_column_field_id(self):
        return 'name'

    @property
    def label(self):
        return _('User Label')


class DisplayNameFilterField(FilterField):
    id = 'name'
    key = 'display_name'
    label = _('Name')


class StatusFilterField(FilterField):
    id = 'status'
    key = 'status'
    label = _('Status')
    include_in_statistics = True

    def __init__(self, formdef):
        super().__init__(formdef=formdef)
        if self.formdef:
            self.waitpoint_status = self.formdef.workflow.get_waitpoint_status()

    @property
    def available_for_filter(self):
        return bool(self.formdef is None or self.waitpoint_status)

    def get_filter_widget(self, mode=None):
        filter_field_value = self.get_filter_field_value()
        r = TemplateIO(html=True)
        operators = [
            ('eq', '='),
            ('ne', '!='),
            ('in', _('in')),
            ('not_in', _('not in')),
        ]
        r += htmltext('<div class="widget operator-and-value-widget">')
        r += htmltext('<div class="title-and-operator">')
        r += htmltext('<div class="title">%s</div>') % _('Status to display')
        filter_field_operator = self.get_filter_field_operator()
        is_multi_values = filter_field_operator in ['in', 'not_in', 'between']
        if mode != 'stats':
            r += htmltext('<div class="operator">')
            operator_widget = SingleSelectWidget(
                'filter-operator',
                options=[(o[0], o[1], o[0]) for o in operators],
                value=self.get_filter_field_operator(),
                render_br=False,
            )
            r += operator_widget.render_content()
            r += htmltext('</div>')
        r += htmltext('</div>')
        r += htmltext('<div class="value content">')
        filter_field_value = self.get_filter_field_value()
        if is_multi_values and filter_field_value:
            r += htmltext('<select name="filter" data-multi-values="%s">' % filter_field_value)
        else:
            r += htmltext('<select name="filter">')
        filters = [
            ('waiting', _('Waiting for an action'), None),
            ('all', _('All'), None),
            ('pending', pgettext_lazy('formdata', 'Open'), None),
            ('done', _('Done'), None),
        ]
        for status in self.waitpoint_status:
            filters.append((status.id, status.name, status.colour))
        for filter_id, filter_label, filter_colour in filters:
            if filter_id == filter_field_value:
                selected = ' selected="selected"'
            else:
                selected = ''
            style = ''
            if filter_colour and filter_colour != '#FFFFFF':
                fg_colour = misc.get_foreground_colour(filter_colour)
                style = 'style="background: %s; color: %s;"' % (filter_colour, fg_colour)
            r += htmltext('<option value="%s"%s %s>' % (filter_id, selected, style))
            r += htmltext('%s</option>') % filter_label
        r += htmltext('</select>')
        r += htmltext('</div>')
        r += htmltext('</div>')
        return r.getvalue()


class UserVisibleStatusField(FilterField):
    id = 'user-visible-status'
    key = 'user-visible-status'
    label = _('Status (for user)')
    geolabel_status = _('Status')


class InternalIdFilterField(FilterField):
    id = 'internal-id'
    key = 'internal-id'
    label = _('Identifier')
    available_for_filter = True

    def get_filter_widget(self, **kwargs):
        widget = StringWidget(
            self.filter_field_key,
            title=self.label,
            value=self.get_filter_field_value(),
            render_br=False,
        )
        return self.render_filter_widget(widget)


class AbstractPeriodFilterField(FilterField):
    available_for_filter = True

    def get_filter_widget(self, **kwargs):
        return DateWidget(
            self.filter_field_key, title=self.label, value=self.get_filter_field_value(), render_br=False
        ).render()


class PeriodStartFilterField(AbstractPeriodFilterField):
    id = 'start'
    key = 'period-date'
    label = _('Start')


class PeriodEndFilterField(AbstractPeriodFilterField):
    id = 'end'
    key = 'period-date'
    label = _('End')


class PeriodStartUpdateTimeFilterField(AbstractPeriodFilterField):
    id = 'start-mtime'
    key = 'period-date'
    label = _('Start (modification time)')


class PeriodEndUpdateTimeFilterField(AbstractPeriodFilterField):
    id = 'end-mtime'
    key = 'period-date'
    label = _('End (modification time)')


class UserIdFilterField(FilterField):
    id = 'user'
    key = 'user-id'
    label = _('User')
    available_for_filter = True

    def get_allowed_operators(self):
        return []

    def get_filter_widget(self, **kwargs):
        filter_field_value = self.get_filter_field_value()
        options = [
            ('', _('None'), ''),
            ('__current__', _('Current user'), '__current__'),
        ]
        if filter_field_value and filter_field_value != '__current__':
            try:
                filtered_user = get_publisher().user_class.get(filter_field_value)
            except KeyError:
                filtered_user = None
            filtered_user_value = filtered_user.display_name if filtered_user else _('Unknown')
            options += [(filter_field_value, filtered_user_value, filter_field_value)]
        widget = SingleSelectWidget(
            self.filter_field_key,
            title=self.label,
            options=options,
            value=filter_field_value,
            render_br=False,
        )
        return self.render_filter_widget(widget)


class UserFunctionFilterField(FilterField):
    id = 'user-function'
    key = 'user-function'
    label = _('User Function')
    available_for_filter = True

    def get_allowed_operators(self):
        return []

    def get_filter_widget(self, **kwargs):
        options = [('', '', '')] + [(x[0], x[1], x[0]) for x in self.formdef.workflow.get_sorted_functions()]
        widget = SingleSelectWidget(
            self.filter_field_key,
            title=self.label,
            options=options,
            value=self.get_filter_field_value(),
            render_br=False,
        )
        return self.render_filter_widget(widget)


class SubmissionAgentFilterField(FilterField):
    id = 'submission-agent'
    key = 'submission-agent'
    label = _('Submission Agent')

    @property
    def available_for_filter(self):
        return bool(self.formdef.backoffice_submission_roles)

    def get_filter_widget(self, **kwargs):
        filter_field_value = self.get_filter_field_value()
        options = [
            ('', '', ''),
            ('__current__', _('Current user'), '__current__'),
        ]
        if filter_field_value == '-1':
            # this happens when ?filter-submission-agent-uuid is given with an unknown uuid,
            # an option for "invalid user" is added so refreshs or new filters won't reset
            # this filter.
            options.append(('-1', _('Invalid user'), '-1'))
        options.extend(
            [
                (str(x.id), x.display_name, str(x.id))
                for x in get_publisher().user_class.select(
                    [
                        Or(
                            [
                                ArrayContains('roles', [str(y)])
                                for y in self.formdef.backoffice_submission_roles
                            ]
                        )
                    ],
                    order_by='ascii_name',
                )
            ]
        )
        widget = SingleSelectWidget(
            self.filter_field_key,
            title=self.label,
            options=options,
            value=filter_field_value,
            render_br=False,
        )
        return self.render_filter_widget(widget)


class SubmissionChannelFilterField(FilterField):
    id = 'submission_channel'
    key = 'submission_channel'
    label = _('Channel')


class CriticalityLevelFilterFiled(FilterField):
    id = 'criticality-level'
    key = 'criticality-level'
    label = _('Criticality Level')

    @property
    def available_for_filter(self):
        return bool(self.formdef.workflow.criticality_levels)

    def get_allowed_operators(self):
        return []

    def get_filter_widget(self, **kwargs):
        options = [('', pgettext_lazy('criticality-level', 'All'), '')] + [
            (str(i), x.name, str(i)) for i, x in enumerate(self.formdef.workflow.criticality_levels)
        ]
        widget = SingleSelectWidget(
            self.filter_field_key,
            title=self.label,
            options=options,
            value=self.get_filter_field_value(),
            render_br=False,
        )
        return self.render_filter_widget(widget)


class DigestFilterField(FilterField):
    id = 'digest'
    key = 'digest'
    label = _('Digest')


class IdFilterField(FilterField):
    id = 'id'
    key = 'id'

    def __init__(self, formdef):
        super().__init__(formdef=formdef)
        self.label = force_str(_('Identifier') if self.formdef.id_template else _('Number'))


class TimeFilterField(FilterField):
    id = 'time'
    key = 'time'
    label = _('Created')


class LastUpdateFilterField(FilterField):
    id = 'last_update_time'
    key = 'last_update_time'
    label = _('Last Modified')


class AnonymisedFilterField(FilterField):
    id = 'anonymised'
    key = 'anonymised'
    label = _('Anonymised')


class NumberFilterField(FilterField):
    id = 'number'
    key = 'number'
    label = _('Number')
    available_for_filter = True


class IdentifierFilterField(FilterField):
    id = 'identifier'
    key = 'identifier'
    label = _('Identifier')
    available_for_filter = True


class DistanceFilterField(FilterField):
    id = 'distance'
    key = 'distance'
    label = _('Distance')
    available_for_filter = True


class CardIdField:
    can_include_in_listing = True
    available_for_filter = False
    key = 'card-id-field'
    store_display_value = None
    store_structured_value = None

    def __init__(self, field):
        self.field = field
        self.parent_field_id = field.id
        self.contextual_id = f'{field.id}_raw'
        self.contextual_varname = f'{field.id}_raw'
        self.id = self.contextual_id
        self.label = '%s / %s' % (field.label, _('Identifier'))

    def get_csv_heading(self):
        return [self.label]

    def get_csv_value(self, element, **kwargs):
        return [element]
