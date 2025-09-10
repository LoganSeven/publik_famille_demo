# w.c.s. - web application for online forms
# Copyright (C) 2005-2021  Entr'ouvert
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

import collections

from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.urls import reverse
from django.views.generic import View
from quixote.errors import RequestError

import wcs.qommon.storage as st
from wcs import sql
from wcs.api_utils import get_user_from_api_query_string, is_url_signed
from wcs.backoffice.data_management import CardPage
from wcs.backoffice.management import FormPage
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.formdata import FormData
from wcs.formdef import FormDef
from wcs.qommon import _, misc, pgettext_lazy
from wcs.qommon.errors import TraversalError
from wcs.sql_criterias import Contains, Equal, Nothing, Null, Or, StrictNotEqual


class RestrictedView(View):
    def dispatch(self, *args, **kwargs):
        if not is_url_signed() and not get_user_from_api_query_string():
            return HttpResponseForbidden()
        return super().dispatch(*args, **kwargs)


class IndexView(RestrictedView):
    def get(self, request, *args, **kwargs):
        channel_options = [
            {'id': '_all', 'label': pgettext_lazy('channel', 'All')},
            {'id': 'backoffice', 'label': _('Backoffice')},
        ] + [{'id': key, 'label': label} for key, label in FormData.get_submission_channels().items()]

        available_statistics = [
            {
                'name': _('Forms Count'),
                'url': request.build_absolute_uri(reverse('api-statistics-forms-count')),
                'id': 'forms_counts',
                'filters': [
                    {
                        'id': 'time_interval',
                        'label': _('Interval'),
                        'options': [
                            {
                                'id': 'day',
                                'label': _('Day'),
                            },
                            {
                                'id': 'month',
                                'label': _('Month'),
                            },
                            {
                                'id': 'year',
                                'label': _('Year'),
                            },
                            {
                                'id': 'weekday',
                                'label': _('Week day'),
                            },
                            {
                                'id': 'hour',
                                'label': _('Hour'),
                            },
                            {
                                'id': 'none',
                                'label': _('None'),
                            },
                        ],
                        'required': True,
                        'default': 'month',
                    },
                    {
                        'id': 'channel',
                        'label': _('Channel'),
                        'options': channel_options,
                        'required': True,
                        'default': '_all',
                    },
                    {
                        'id': 'form',
                        'label': _('Form(s)'),
                        'options': self.get_form_options(FormDef),
                        'has_subfilters': True,
                        'multiple': True,
                    },
                ],
            },
        ]

        if CardDef.count():
            available_statistics.append(
                {
                    'name': _('Cards Count'),
                    'url': request.build_absolute_uri(reverse('api-statistics-cards-count')),
                    'id': 'cards_counts',
                    'filters': [
                        {
                            'id': 'time_interval',
                            'label': _('Interval'),
                            'options': [
                                {
                                    'id': 'day',
                                    'label': _('Day'),
                                },
                                {
                                    'id': 'month',
                                    'label': _('Month'),
                                },
                                {
                                    'id': 'year',
                                    'label': _('Year'),
                                },
                                {
                                    'id': 'weekday',
                                    'label': _('Week day'),
                                },
                                {
                                    'id': 'hour',
                                    'label': _('Hour'),
                                },
                                {
                                    'id': 'none',
                                    'label': _('None'),
                                },
                            ],
                            'required': True,
                            'default': 'month',
                            'has_subfilters': True,
                        },
                        {
                            'id': 'form',
                            'label': _('Card'),
                            'options': self.get_form_options(CardDef, include_all_option=False),
                            'required': True,
                            'has_subfilters': True,
                        },
                    ],
                },
            )

        return JsonResponse(
            {
                'data': available_statistics
                + [
                    {
                        'name': _('Time between two statuses (forms)'),
                        'url': request.build_absolute_uri(reverse('api-statistics-resolution-time')),
                        'id': 'resolution_time',
                        'data_type': 'seconds',
                        'filters': [
                            {
                                'id': 'form',
                                'label': _('Form(s)'),
                                'options': self.get_form_options(FormDef),
                                'has_subfilters': True,
                                'multiple': True,
                            },
                        ],
                    },
                    {
                        'name': _('Time between two statuses (cards)'),
                        'url': request.build_absolute_uri(reverse('api-statistics-resolution-time-cards')),
                        'id': 'resolution_time_cards',
                        'data_type': 'seconds',
                        'filters': [
                            {
                                'id': 'form',
                                'label': _('Card'),
                                'options': self.get_form_options(CardDef, include_all_option=False),
                                'required': True,
                                'has_subfilters': True,
                            },
                        ],
                    },
                ]
            }
        )

    @staticmethod
    def get_form_options(formdef_class, include_all_option=True):
        forms = formdef_class.select(lightweight=True)
        forms.sort(key=lambda x: misc.simplify(x.name))

        forms_with_category = [x for x in forms if x.category]
        if not forms_with_category:
            form_options = [{'id': x.url_name, 'label': x.name} for x in forms]
            return form_options

        form_options = collections.defaultdict(list)
        for x in forms_with_category:
            if x.category.name not in form_options and include_all_option:
                form_options[x.category.name] = [
                    {
                        'id': 'category:' + x.category.url_name,
                        'label': _('All forms of category %s') % x.category.name,
                    }
                ]
            form_options[x.category.name].append({'id': x.url_name, 'label': x.name})
        form_options = sorted(
            ((category, forms) for category, forms in form_options.items()), key=lambda x: misc.simplify(x[0])
        )

        forms_without_category_options = [
            {'id': x.url_name, 'label': x.name} for x in forms if not x.category
        ]
        if forms_without_category_options:
            form_options.append((_('Misc'), forms_without_category_options))

        return form_options


class FormsCountView(RestrictedView):
    formdef_class = FormDef
    formpage_class = FormPage
    has_global_count_support = True
    label = _('Forms Count')

    def get_formdefs_from_url(self, request):
        slugs = request.GET.getlist('form', ['_all'] if self.has_global_count_support else ['_nothing'])
        if slugs == ['_all']:
            return []

        formdef_slugs = [x for x in slugs if not x.startswith('category:')]
        criterias = [st.Contains('slug', formdef_slugs)]
        self.filter_by_category(slugs, criterias)

        formdefs = self.formdef_class.select([st.Or(criterias)], order_by='name')

        if not formdefs:
            raise TraversalError()

        for formdef in formdefs:
            formdef.form_page = self.formpage_class(formdef=formdef, update_breadcrumbs=False)

        return formdefs

    def get(self, request, *args, **kwargs):
        time_interval = request.GET.get('time_interval', 'month')
        totals_kwargs = {
            'period_start': request.GET.get('start'),
            'period_end': request.GET.get('end'),
            'criterias': [StrictNotEqual('status', 'draft')],
        }
        group_by = request.GET.get('group-by')
        group_labels = {}

        formdefs = self.get_formdefs_from_url(request)

        include_subfilters = request.GET.get('include-subfilters', False)
        subfilters = self.get_common_subfilters(time_interval, formdefs) if include_subfilters else []

        if formdefs:
            self.set_formdef_parameters(totals_kwargs, formdefs)
            totals_kwargs['criterias'].extend(self.get_filters_criterias(formdefs))
            if include_subfilters:
                self.add_formdefs_subfilters(subfilters, formdefs, group_by, time_interval)

        self.set_group_by_parameters(group_by, formdefs, totals_kwargs, group_labels)

        channel = request.GET.get('channel', '_all')
        if channel in ('web', 'backoffice'):
            totals_kwargs['criterias'].append(
                Or(
                    [
                        Equal('submission_channel', 'web'),
                        Equal('submission_channel', ''),
                        Null('submission_channel'),
                    ]
                )
            )
            totals_kwargs['criterias'].append(Equal('backoffice_submission', bool(channel == 'backoffice')))
        elif channel != '_all':
            totals_kwargs['criterias'].append(Equal('submission_channel', channel))

        time_interval_methods = {
            'day': sql.get_daily_totals,
            'month': sql.get_monthly_totals,
            'year': sql.get_yearly_totals,
            'weekday': sql.get_weekday_totals,
            'hour': sql.get_hour_totals,
            'none': sql.get_global_totals,
        }
        if time_interval in time_interval_methods:
            totals = time_interval_methods[time_interval](**totals_kwargs)
        else:
            return HttpResponseBadRequest('invalid time_interval parameter')

        if 'group_by' not in totals_kwargs:
            x_labels = [x[0] for x in totals]
            series = [{'label': self.label, 'data': [x[1] for x in totals]}]
        elif time_interval == 'none':
            x_labels, series = self.get_grouped_data(totals, group_labels)
        else:
            x_labels, series = self.get_grouped_time_data(totals, group_labels)

        months_to_show = request.GET.get('months_to_show', '_all')
        if time_interval == 'month' and months_to_show != '_all':
            try:
                months_to_show = int(months_to_show)
            except (ValueError, TypeError):
                pass
            else:
                x_labels = x_labels[-months_to_show:]
                for serie in series:
                    serie['data'] = serie['data'][-months_to_show:]
                series = [serie for serie in series if any(serie['data'])]

        return JsonResponse(
            {'data': {'x_labels': x_labels, 'series': series, 'subfilters': subfilters}, 'err': 0}
        )

    def filter_by_category(self, slugs, criterias):
        category_slugs = [x.split(':', 1)[1] for x in slugs if x.startswith('category:')]
        categories = Category.select([st.Contains('slug', category_slugs)], ignore_errors=True)
        category_ids = [x.id for x in categories]
        criterias.append(st.Contains('category_id', category_ids))

    def set_formdef_parameters(self, totals_kwargs, formdefs):
        # set formdef_klass to None to deactivate switching to formdef specific table
        totals_kwargs['criterias'].append(Equal('formdef_klass', None))
        totals_kwargs['criterias'].append(Contains('formdef_id', [x.id for x in formdefs]))

    def transform_criteria(self, criteria):
        if not hasattr(criteria, 'field'):
            return criteria

        attribute = "statistics_data->'%s'" % criteria.field.varname

        if isinstance(criteria.value, bool):
            value = str(criteria.value).lower()
        else:
            value = '"%s"' % criteria.value

        return sql.ArrayContains(attribute, value)

    def get_filters_criterias(self, formdefs):
        criterias = []
        for criteria in formdefs[0].form_page.get_criterias_from_query(statistics_fields_only=True):
            if isinstance(criteria, Nothing):
                continue

            for formdef in formdefs[1:]:
                varnames = {x.contextual_varname for x in self.get_form_fields(formdef.form_page)}
                if criteria.field.contextual_varname not in varnames:
                    break
            else:
                criterias.append(criteria)

        criterias = [self.transform_criteria(criteria) for criteria in criterias]

        selected_status = self.request.GET.get('filter-status')
        applied_filters = None
        if selected_status and selected_status != '_all':
            if selected_status == 'pending':
                applied_filters = [
                    'wf-%s' % x.id for formdef in formdefs for x in formdef.workflow.get_not_endpoint_status()
                ]
            elif selected_status == 'done':
                applied_filters = [
                    'wf-%s' % x.id for formdef in formdefs for x in formdef.workflow.get_endpoint_status()
                ]
            else:
                try:
                    for formdef in formdefs:
                        formdef.workflow.get_status(selected_status)
                    applied_filters = ['wf-%s' % selected_status]
                except KeyError:
                    pass

        if applied_filters:
            criterias.append(Contains('status', applied_filters))
        else:
            criterias = [StrictNotEqual('status', 'draft')] + criterias

        return criterias

    def get_common_subfilters(self, time_interval, formdefs):
        subfilters = []

        if time_interval == 'month':
            subfilters.append(
                {
                    'id': 'months_to_show',
                    'label': _('Number of months to show'),
                    'options': [
                        {'id': '_all', 'label': _('All')},
                        {'id': '6', 'label': _('Last six months')},
                        {'id': '12', 'label': _('Last twelve months')},
                    ],
                    'required': True,
                    'default': '_all',
                }
            )

        group_by_filter = {
            'id': 'group-by',
            'label': _('Group by'),
            'has_subfilters': True,
            'options': [
                {'id': 'channel', 'label': _('Channel')},
            ],
        }

        if len(formdefs) != 1:
            # allow grouping by form only if no form is selected or many are selected
            group_by_filter['options'].append({'id': 'form', 'label': _('Form')})

        subfilters.append(group_by_filter)

        return subfilters

    def get_formdefs_subfilters(self, formdefs, include_status=True):
        subfilters = None
        for formdef in formdefs:
            new_subfilters = self.get_form_subfilters(formdef.form_page, include_status=include_status)
            new_subfilters = {x['id']: x for x in new_subfilters}

            if subfilters is None:
                subfilters = new_subfilters
                continue

            # keep only common subfilters
            subfilters = {k: v for k, v in subfilters.items() if k in new_subfilters}

            for filter_id, subfilter in subfilters.copy().items():
                if subfilter['options'] != new_subfilters[filter_id]['options']:
                    if filter_id in ('filter-status'):
                        # keep only common options
                        subfilter['options'] = {
                            k: v
                            for k, v in subfilter['options'].items()
                            if k in new_subfilters[filter_id]['options']
                        }
                    else:
                        # merge all options for standard filter
                        subfilter['options'].update(new_subfilters[filter_id]['options'])
                        subfilter['options']['needs_sorting'] = True

        subfilters = list(subfilters.values())
        for subfilter in subfilters:
            needs_sorting = subfilter['options'].pop('needs_sorting', False)
            subfilter['options'] = [
                {'id': option, 'label': label} for option, label in subfilter['options'].items()
            ]
            if needs_sorting:
                subfilter['options'].sort(key=lambda x: x['label'])

        return subfilters

    def add_formdefs_subfilters(self, common_subfilters, formdefs, group_by, time_interval):
        subfilters = self.get_formdefs_subfilters(formdefs)

        group_by_filter = [x for x in common_subfilters if x['id'] == 'group-by'][0]
        group_by_filter['options'].append({'id': 'simple-status', 'label': _('Simplified status')})
        group_by_filter['options'].extend(
            [{'id': x['id'].removeprefix('filter-'), 'label': x['label']} for x in subfilters]
        )

        if group_by not in (None, 'channel', 'simple-status', 'status'):
            group_by_field = self.get_group_by_field(formdefs[0].form_page, group_by)
            if group_by_field:
                common_subfilters.append(
                    {
                        'id': 'hide_none_label',
                        'label': _('Ignore forms where "%s" is empty.') % group_by_field.label,
                        'options': [{'id': 'true', 'label': _('Yes')}, {'id': 'false', 'label': _('No')}],
                        'required': True,
                        'default': 'false',
                    }
                )

        common_subfilters.extend(subfilters)

    @staticmethod
    def get_form_fields(form_page):
        if hasattr(form_page, '_statistics_field'):
            return form_page._statistics_field

        fields = [
            field
            for field in form_page.get_formdef_fields(include_block_items_fields=True)
            if getattr(field, 'include_in_statistics', False) and field.contextual_varname
        ]

        form_page._statistics_field = fields
        return fields

    def get_form_subfilters(self, form_page, include_status=True):
        subfilters = []
        for field in self.get_form_fields(form_page):
            field_key = 'filter-%s' % field.contextual_varname
            field.required = False

            if field.key == 'status' and include_status:
                waitpoint_status = form_page.formdef.workflow.get_waitpoint_status()
                if not waitpoint_status:
                    continue

                field.required = True
                field.default_filter_value = '_all'
                options = [
                    ('_all', _('All')),
                    ('pending', pgettext_lazy('statistics', 'Open')),
                    ('done', pgettext_lazy('statistics', 'Done')),
                ]
                for status in waitpoint_status:
                    options.append((status.id, status.name))
            elif field.key in ('item', 'items'):
                options = form_page.get_item_filter_options(field, selected_filter='all', anonymised=True)
                if not options:
                    continue
            elif field.key == 'bool':
                options = [('true', _('Yes')), ('false', _('No'))]
            else:
                continue

            filter_description = {
                'id': field_key,
                'label': field.label,
                'options': {x[0]: x[1] for x in options},
                'required': field.required,
            }
            if hasattr(field, 'default_filter_value'):
                filter_description['default'] = field.default_filter_value

            subfilters.append(filter_description)

        return subfilters

    def get_group_by_field(self, form_page, group_by):
        fields = [x for x in self.get_form_fields(form_page) if x.contextual_varname == group_by]
        if fields:
            return fields[0]

    def get_group_labels(self, formdef, group_by):
        group_labels = {}
        if group_by == 'status':
            group_labels = {'wf-%s' % status.id: status.name for status in formdef.workflow.possible_status}
        elif group_by == 'simple-status':
            group_labels['wf-%s' % formdef.workflow.possible_status[0].id] = _('New')
            for status in formdef.workflow.possible_status[1:]:
                if status.is_endpoint():
                    group_labels['wf-%s' % status.id] = _('Done')
                else:
                    group_labels['wf-%s' % status.id] = _('In progress')
        elif formdef.group_by_field.key == 'bool':
            group_labels = {True: _('Yes'), False: _('No')}
        elif formdef.group_by_field.key in ('item', 'items'):
            options = formdef.form_page.get_item_filter_options(
                formdef.group_by_field, selected_filter='all', anonymised=True
            )
            group_labels = {option[0]: option[1] for option in options}

        group_labels[None] = _('None')
        return group_labels

    def set_group_by_parameters(self, group_by, formdefs, totals_kwargs, group_labels):
        if not group_by:
            return

        if group_by == 'form':
            totals_kwargs['group_by'] = 'formdef_id'
            group_labels.update(
                {int(x.id): x.name for x in self.formdef_class.select(lightweight=True, order_by='name')}
            )
            return

        if group_by == 'channel':
            totals_kwargs['group_by'] = 'submission_channel_new'
            totals_kwargs['group_by_clause'] = (
                'CASE '
                "WHEN submission_channel IN ('web', '') OR submission_channel IS NULL THEN "
                "CASE WHEN backoffice_submission THEN 'backoffice' ELSE 'web' END "
                'ELSE submission_channel '
                'END '
                'as submission_channel_new, '
            )

            group_labels.update(FormData.get_submission_channels())
            group_labels['backoffice'] = _('Backoffice')
            return

        if not formdefs:
            # only form and channel subfilters are allowed without form filter
            return

        if group_by == 'simple-status':
            group_by_key = 'status'
        else:
            group_by_key = group_by

        for formdef in formdefs:
            formdef.group_by_field = self.get_group_by_field(formdef.form_page, group_by_key)
            if not formdef.group_by_field:
                return

        if group_by_key == 'status':
            totals_kwargs['group_by'] = 'status'
        else:
            totals_kwargs['group_by'] = "statistics_data->'%s'" % formdefs[0].group_by_field.varname

        if self.request.GET.get('hide_none_label') == 'true':
            totals_kwargs['criterias'].append(StrictNotEqual(totals_kwargs['group_by'], '[]'))

        for formdef in formdefs:
            group_labels.update(self.get_group_labels(formdef, group_by))

    def get_grouped_time_data(self, totals, group_labels):
        totals_by_time = collections.OrderedDict(
            # time1: {group1: total_11, group2: total_12},
            # time2: {group1: total_21}
        )
        seen_group_values = set(
            # group1, group2
        )
        for total in totals:
            totals_by_group = totals_by_time.setdefault(total[0], collections.Counter())
            if len(total) == 2:
                # ignore empty value used to fill time gaps
                continue
            groups = total[1]
            if not isinstance(groups, list):
                groups = [groups]
            if not groups:
                groups = [None]
            for group in groups:
                totals_by_group[group] += total[2]
                seen_group_values.add(group)

        totals_by_group = {
            # group1: [total_11, total_21],
            # group2: [total_12, None],
        }
        for group in seen_group_values:
            totals_by_group[group] = [totals.get(group) for totals in totals_by_time.values()]

        totals_by_label = self.get_totals_by_label(totals_by_group, group_labels)

        x_labels = list(totals_by_time)
        series = [{'label': label, 'data': data} for label, data in totals_by_label.items()]
        return x_labels, series

    def get_grouped_data(self, totals, group_labels):
        totals_by_group = collections.Counter()
        for groups, total in totals:
            if not isinstance(groups, list):
                groups = [groups]
            if not groups:
                groups = [None]
            for group in groups:
                totals_by_group[group] += total

        totals_by_label = self.get_totals_by_label(totals_by_group, group_labels)

        x_labels = list(totals_by_label)
        series = [{'label': self.label, 'data': [total for total in totals_by_label.values()]}]
        return x_labels, series

    def sort_by_label(self, data, group_labels, key=lambda x: x):
        group_label_indexes = {group: i for i, group in enumerate(group_labels)}

        def get_group_order(group):
            if group is None:
                # None choice should always be last
                return len(group_label_indexes) + 1
            if group not in group_label_indexes:
                # unknown group should be last but before none
                return len(group_label_indexes)
            return group_label_indexes[group]

        data.sort(key=lambda x: get_group_order(key(x)))

    def get_totals_by_label(self, totals_by_group, group_labels):
        groups = list(totals_by_group)
        self.sort_by_label(groups, group_labels)

        totals_by_label = {}
        for group in groups:
            label = group_labels.get(group, group)
            if label in totals_by_label:
                if isinstance(totals_by_label[label], list):
                    for i, (x, y) in enumerate(zip(totals_by_group[group], totals_by_label[label])):
                        totals_by_label[label][i] = (x or 0) + (y or 0) if x or y else None
                        totals_by_label[label][i] = ((x or 0) + (y or 0)) or None
                else:
                    totals_by_label[label] = (
                        (totals_by_label[label] or 0) + (totals_by_group[group] or 0)
                    ) or None
            else:
                totals_by_label[label] = totals_by_group[group]

        return totals_by_label


class CardsCountView(FormsCountView):
    formdef_class = CardDef
    formpage_class = CardPage
    has_global_count_support = False
    label = _('Cards Count')

    def set_formdef_parameters(self, totals_kwargs, formdefs):
        # formdef_klass is a fake criteria, it will be used in time interval functions
        # to switch to appropriate class, it must appear before formdef_id.
        totals_kwargs['criterias'].append(Equal('formdef_klass', CardDef))
        totals_kwargs['criterias'].append(Equal('formdef_id', formdefs[0].id))

    def filter_by_category(self, slugs, criterias):
        pass  # unsupported


class ResolutionTimeView(FormsCountView):
    formdef_class = FormDef
    label = _('Time between two statuses (forms)')

    def get(self, request, *args, **kwargs):
        formdefs = self.get_formdefs_from_url(request)

        include_subfilters = request.GET.get('include-subfilters', False)
        subfilters = self.get_subfilters(formdefs) if include_subfilters else []

        group_by = self.request.GET.get('group-by')
        group_labels, group_by_data = {}, {}
        self.set_group_by_parameters(group_by, formdefs, group_by_data, group_labels)

        if len(formdefs) == 1:
            results = self.get_form_statistics(formdefs[0], group_by_data.get('group_by'), group_labels)
        else:
            results = self.get_forms_statistics(formdefs, group_by_data.get('group_by'), group_labels)

        return JsonResponse(
            {
                'data': {
                    'x_labels': [_('Minimum time'), _('Maximum time'), _('Mean'), _('Median')],
                    'series': [{'label': label, 'data': serie} for label, serie in results],
                    'subfilters': subfilters,
                },
                'err': 0,
            }
        )

    def get_subfilters(self, formdefs):
        if len(formdefs) == 1:
            status_options = [
                {'id': status.id, 'label': status.name} for status in formdefs[0].workflow.possible_status
            ]
        else:
            status_options = [{'id': 'creation', 'label': _('Any initial status')}]

        form_subfilters = []
        if formdefs:
            form_subfilters = self.get_formdefs_subfilters(formdefs, include_status=False)

        if form_subfilters:
            form_subfilters.insert(
                0,
                {
                    'id': 'group-by',
                    'label': _('Group by'),
                    'options': [
                        {'id': x['id'].removeprefix('filter-'), 'label': x['label']} for x in form_subfilters
                    ],
                },
            )

        return [
            {
                'id': 'start_status',
                'label': _('Start status'),
                'options': status_options,
                'required': True,
                'default': status_options[0]['id'],
            },
            {
                'id': 'end_status',
                'label': _('End status'),
                'options': [{'id': 'done', 'label': _('Any final status')}] + status_options[1:],
                'required': True,
                'default': 'done',
            },
        ] + form_subfilters

    def get_form_statistics(self, formdef, group_by, group_labels):
        start_status = self.request.GET.get('start_status', formdef.workflow.possible_status[0].id)
        end_status = self.request.GET.get('end_status', 'done')

        try:
            start_status = formdef.workflow.get_status(start_status)
        except KeyError:
            start_status = formdef.workflow.possible_status[0]

        end_statuses = None
        if end_status != 'done':
            try:
                end_status = formdef.workflow.get_status(end_status)
            except KeyError:
                end_status = 'done'
            else:
                end_statuses = {'wf-%s' % end_status.id}

        if not end_statuses:
            end_statuses = {'wf-%s' % status.id for status in formdef.workflow.get_endpoint_status()}

        if not end_statuses:
            raise RequestError(_('No final status in workflow.'))

        label = _('Time between %(start_status)s and %(end_status)s') % {
            'start_status': _('"%s"') % start_status.name,
            'end_status': _('"%s"') % end_status.name if end_status != 'done' else _('any final status'),
        }

        res_time_forms = formdef.data_class().get_resolution_times(
            start_status='wf-%s' % start_status.id,
            end_statuses=end_statuses,
            period_start=self.request.GET.get('start'),
            period_end=self.request.GET.get('end'),
            criterias=self.get_filters_criterias([formdef]),
            group_by=group_by,
        )

        return self.aggregate_resolution_times(res_time_forms, label, group_by, group_labels)

    def aggregate_resolution_times(self, res_time_forms, label, group_by, group_labels):
        if not res_time_forms:
            return [(label, [])]

        res_times_by_group = collections.defaultdict(list)
        for res_time, groups in res_time_forms:
            for group in groups or [None]:
                res_times_by_group[group].append(res_time)

        labels = group_labels if group_by else {None: label}
        results = [
            (labels.get(group, group), self.get_computed_times(res_times))
            for group, res_times in res_times_by_group.items()
        ]

        self.sort_by_label(results, group_labels, key=lambda x: x[0])

        return results

    def get_computed_times(self, res_time_forms):
        sum_times = sum(res_time_forms)
        len_times = len(res_time_forms)
        mean = sum_times // len_times

        if len_times % 2:
            median = res_time_forms[len_times // 2]
        else:
            midpt = len_times // 2
            median = (res_time_forms[midpt - 1] + res_time_forms[midpt]) // 2

        return [res_time_forms[0], res_time_forms[-1], mean, median]

    def get_forms_statistics(self, formdefs, group_by, group_labels):
        criterias = [StrictNotEqual('status', 'draft')]
        if formdefs:
            criterias.extend(self.get_filters_criterias(formdefs))
            criterias.append(Contains('formdef_id', [x.id for x in formdefs]))

        res_time_forms = sql.get_resolution_times(
            period_start=self.request.GET.get('start'),
            period_end=self.request.GET.get('end'),
            criterias=criterias,
            group_by=group_by,
        )

        label = _('Time between creation and any final status')
        return self.aggregate_resolution_times(res_time_forms, label, group_by, group_labels)


class CardsResolutionTimeView(ResolutionTimeView):
    label = _('Time between two statuses (cards)')
    formdef_class = CardDef
    has_global_count_support = False
