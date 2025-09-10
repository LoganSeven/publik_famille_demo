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

import datetime
import functools
import json
import operator
import re
import time
import warnings

from django.utils import formats
from django.utils.encoding import force_str
from django.utils.timezone import is_aware, make_naive, now
from quixote import get_publisher, get_request, get_session
from quixote.errors import RequestError

from wcs.sql_criterias import (
    And,
    Between,
    BlockAbsent,
    Contains,
    Distance,
    Equal,
    Greater,
    GreaterOrEqual,
    IEqual,
    ILike,
    Less,
    LessOrEqual,
    Not,
    NotEqual,
    Nothing,
    NotNull,
    Null,
    Or,
    StrictNotEqual,
)

from .fields.item import UnknownCardValueError
from .formdata import get_workflow_roles_substitution_variables
from .qommon import _, misc
from .qommon.evalutils import make_datetime
from .qommon.substitution import CompatibilityNamesDict
from .qommon.templatetags.qommon import parse_datetime
from .utils import add_timing_mark


def is_supported_slice(slice_):
    return isinstance(slice_, slice) and not slice_.step


class LazyFormDefObjectsManager:
    # noqa pylint: disable=too-many-public-methods

    def __init__(
        self,
        formdef,
        formdata=None,
        geoloc_center_formdata=None,
        criterias=None,
        order_by=None,
        limit=None,
        report_error_type=None,
        slice=None,
    ):
        self._formdef = formdef
        self._formdata = formdata
        self._geoloc_center_formdata = geoloc_center_formdata
        if criterias is None:
            criterias = [
                StrictNotEqual('status', 'draft'),
                Null('anonymised'),
            ]
            # add custom marker to criteria so it can be found back and removed in
            # drafts() or with_anonymised()
            criterias[0].exclude_drafts = True
            criterias[1].exclude_anonymised = True
        self._criterias = criterias
        self._order_by = order_by
        self._limit = limit
        self._slice = slice
        self._cached_resultset = None
        self._report_error_type = report_error_type or 'record-error'

    def report_error(self, error_message):
        if self._report_error_type == 'request-error':
            raise RequestError(error_message)
        if self._report_error_type == 'session-error':
            get_session().add_message(error_message, level='warning')
        if self._report_error_type == 'record-error':
            get_publisher().record_error(
                error_message,
                formdata=self._formdata,
            )

    @property  # @property for backward compatibility
    def count(self):
        if not hasattr(self, '_count_cache'):
            if self._slice:
                self._populate_cache()
                self._count_cache = len(self._cached_resultset)
            else:
                self._count_cache = self.total_count()
        return self._count_cache

    def total_count(self):
        if not hasattr(self, '_total_count_cache'):
            self._total_count_cache = self._formdef.data_class().count(clause=self._criterias)
        return self._total_count_cache

    def _clone(self, criterias, order_by=None, slice=None):
        return LazyFormDefObjectsManager(
            formdef=self._formdef,
            formdata=self._formdata,
            geoloc_center_formdata=self._geoloc_center_formdata,
            criterias=criterias,
            order_by=order_by or self._order_by,
            limit=self._limit,
            slice=slice or self._slice,
        )

    def order_by(self, attribute):
        if not isinstance(attribute, str):
            self.report_error(
                _('Invalid value %r for "order_by"') % attribute,
            )
            return self.none()
        if attribute.startswith(('form_var_', 'block_var')):
            self.report_error(
                _('Invalid value "%s" for "order_by"') % attribute,
            )
            return self.none()
        field = self.get_field(attribute)
        return self._clone(self._criterias, order_by=field or attribute)

    def limit(self, limit):
        qs = self._clone(self._criterias)
        qs._limit = limit
        return qs

    def all(self):
        # (expose 'all' only to mimick django, it's not actually useful as this
        # object serves as both manager and queryset)
        return self._clone(None)

    def none(self):
        return self._clone([Nothing()])

    def pending(self):
        status_filters = ['wf-%s' % x.id for x in self._formdef.workflow.get_not_endpoint_status()]
        criterias = [Contains('status', status_filters)]
        return self._clone(self._criterias + criterias)

    def current_user(self):  # filter on current user
        return self.filter_by_user(get_request().user)

    def filter_by_user(self, user, op='eq'):
        if op not in ['eq']:
            self.report_error(
                _('Invalid operator "%(operator)s" for filter "%(filter)s"')
                % {'operator': self.get_operator_name(op), 'filter': self.pending_attr},
            )
            return self.none()
        if isinstance(user, str):
            user = get_publisher().user_class.lookup_by_string(user)
        if not user:
            return self.none()
        return self._clone(self._criterias + [Equal('user_id', str(user.id))])

    def filter_by_status(self, status, op='eq'):
        if op not in ['eq', 'ne']:
            self.report_error(
                _('Invalid operator "%(operator)s" for filter "%(filter)s"')
                % {'operator': self.get_operator_name(op), 'filter': self.pending_attr},
            )
            return self.none()
        for wfs in self._formdef.workflow.possible_status:
            if wfs.name == status:
                wf_status = 'wf-%s' % wfs.id
                return self._clone(
                    self._criterias
                    + [self.get_criteria_from_operator(op=op, value=wf_status, field_id='status')]
                )
        return self.none()

    def with_custom_view(self, custom_view_slug):
        lookup_criterias = [
            Equal('formdef_type', self._formdef.xml_root_node),
            Equal('formdef_id', str(self._formdef.id)),
            Contains('visibility', ['any', 'datasource']),
            Equal('slug', custom_view_slug),
        ]
        try:
            custom_view = get_publisher().custom_view_class.select(lookup_criterias)[0]
        except IndexError:
            self.report_error(_('Unknown custom view "%(slug)s"') % {'slug': custom_view_slug})
            return self.none()
        return self._clone(self._criterias + custom_view.get_criterias(), order_by=custom_view.order_by)

    def exclude_self(self):
        assert self._formdata
        if not self._formdata.id:
            edited_id = getattr(self._formdata, '_edited_id', None)
            if not edited_id and self._formdata.data:
                edited_id = self._formdata.data.get('edited_formdata_id')
            if edited_id:
                return self._clone(self._criterias + [StrictNotEqual('id', str(edited_id))])
            return self._clone(self._criterias)
        return self._clone(self._criterias + [StrictNotEqual('id', str(self._formdata.id))])

    def same_user(self):
        assert self._formdata
        if self._formdata.user_id is None:
            return self
        return self._clone(self._criterias + [Equal('user_id', str(self._formdata.user.id))])

    def drafts(self):
        criterias = [x for x in self._criterias if not getattr(x, 'exclude_drafts', False)]
        return self._clone(criterias + [Equal('status', 'draft')])

    def with_anonymised(self):
        criterias = [x for x in self._criterias if not getattr(x, 'exclude_anonymised', True)]
        return self._clone(criterias)

    def with_drafts(self):
        criterias = [x for x in self._criterias if not getattr(x, 'exclude_drafts', False)]
        return self._clone(criterias)

    def done(self):
        status_filters = ['wf-%s' % x.id for x in self._formdef.workflow.get_endpoint_status()]
        criterias = [Contains('status', status_filters)]
        return self._clone(self._criterias + criterias)

    def set_geo_center(self, lazy_formdata):
        qs = self._clone(self._criterias)
        qs._geoloc_center_formdata = lazy_formdata._formdata
        return qs

    def filter_by_distance(self, distance, op=None):
        try:
            distance = int(misc.unlazy(distance))
        except (TypeError, ValueError):
            get_publisher().record_error(_('invalid value for distance (%r)') % distance)
            distance = None
        center = (self._geoloc_center_formdata or self._formdata).get_auto_geoloc()
        if center is None or distance is None:
            return self.none()
        return self._clone(self._criterias + [Distance(center, distance)])

    def filter_by(self, attribute):
        qs = self._clone(self._criterias)
        qs.pending_attr = attribute
        return qs

    def filter_by_internal_id(self, value, op='eq'):
        from wcs.backoffice.filter_fields import InternalIdFilterField

        field = InternalIdFilterField(formdef=self._formdef)
        operators = self.get_field_allowed_operators(field)
        if op not in [o[0] for o in operators]:
            self.report_error(
                _('Invalid operator "%(operator)s" for filter "%(filter)s"')
                % {'operator': self.get_operator_name(op), 'filter': self.pending_attr},
            )
            return self.none()
        if op in ('in', 'not_in'):
            check_values = re.split(r'[\|,]', str(value))
        else:
            check_values = [value]
            value = str(value)
        for check_value in check_values:
            try:
                int(check_value)
            except (ValueError, TypeError):
                self.report_error(
                    _('Invalid value "%s" for filter "internal_id"') % value,
                )
                return self.none()
        if op in ('in', 'not_in'):
            value = check_values
        return self._clone(
            self._criterias + [self.get_criteria_from_operator(op=op, value=value, field_id='id')]
        )

    def filter_by_number(self, value, op='eq'):
        if op not in ['eq']:
            self.report_error(
                _('Invalid operator "%(op)s" for filter "number"') % {'op': self.get_operator_name(op)}
            )
            return self.none()
        return self._clone(self._criterias + [Equal('id_display', str(value))])

    def filter_by_identifier(self, value, op='eq'):
        if op not in ['eq']:
            self.report_error(
                _('Invalid operator "%(op)s" for filter "identifier"') % {'op': self.get_operator_name(op)}
            )
            return self.none()
        return self._clone(self._criterias + [self._formdef.get_by_id_criteria(str(value))])

    def get_fields(self, key):
        for field in self._formdef.iter_fields(include_block_fields=True, with_no_data_fields=False):
            if getattr(field, 'block_field', None):
                if field.key == 'items':
                    # not yet
                    continue
            if field.contextual_varname == key:
                yield field

    def get_field(self, key):
        for field in self.get_fields(key):
            return field

    def get_field_allowed_operators(self, field):
        equality_operators = [
            ('eq', '='),
            ('ne', '!='),
        ]
        comparison_operators = [
            ('lt', '<'),
            ('lte', '<='),
            ('gt', '>'),
            ('gte', '>='),
        ]
        more_comparison_operators = [
            ('between', _('between')),
        ]
        empty_operators = [
            ('absent', _('absent')),
            ('existing', _('existing')),
        ]
        in_operators = [
            ('in', _('in')),
            ('not_in', _('not in')),
        ]
        text_operators = [
            ('icontains', _('contains')),
            ('ieq', _('case-insensitive equals')),
        ]
        date_operators = [
            ('is_today', _('today')),
            ('is_tomorrow', _('tomorrow')),
            ('is_yesterday', _('yesterday')),
            ('is_this_week', _('this week')),
            ('is_future', _('in the future')),
            ('is_past', _('in the past')),
            ('is_today_or_future', _('today or in the future')),
            ('is_today_or_past', _('today or in the past')),
        ]
        if field.key == 'internal-id':
            return equality_operators + comparison_operators + in_operators
        if field.key in ['string', 'text']:
            return (
                equality_operators
                + comparison_operators
                + more_comparison_operators
                + in_operators
                + empty_operators
                + text_operators
            )
        if field.key in ['date']:
            return (
                equality_operators
                + comparison_operators
                + more_comparison_operators
                + in_operators
                + empty_operators
                + date_operators
            )
        if field.key in ['item', 'items', 'numeric']:
            return (
                equality_operators
                + comparison_operators
                + more_comparison_operators
                + in_operators
                + empty_operators
            )
        if field.key == 'bool':
            return equality_operators + empty_operators
        if field.key == 'email':
            return equality_operators + in_operators + empty_operators + text_operators
        if field.key == 'file':
            return empty_operators
        return None

    def format_value(self, op, value, field):
        pending_attr = getattr(self, 'pending_attr', None)
        if not pending_attr:
            pending_attr = field.varname or field.id
        if self._report_error_type == 'request-error':
            pending_attr = 'filter-%s' % pending_attr

        def check_int(val):
            if isinstance(val, str) and '_' in val:
                # do not consider _ a valid character in numbers
                # (unlike python where it can be used to group digits)
                return val
            try:
                # cast to integer so it can be used with numerical operators
                # (limit to 32bits to match postgresql integer range)
                int_value = int(val)
                if -(2**31) <= int_value < 2**31 and (int_value == 0 or str(value)[0] != '0'):
                    return int_value
            except (ValueError, TypeError):
                return str(val)
            return val

        def convert_value(value, field):
            if field.convert_value_from_anything and value is not Ellipsis:
                try:
                    value = field.convert_value_from_anything(value)
                except UnknownCardValueError:
                    # do not report an error if filtering an item field on an unknown value
                    pass
                except (ValueError, AttributeError):
                    self.report_error(
                        _('Invalid value "%(value)s" for filter "%(filter)s"')
                        % {'value': value, 'filter': pending_attr},
                    )
                    raise ValueError
                if value is not None and hasattr(field, 'block_field') and hasattr(field, 'get_json_value'):
                    # in block fields, we store the whole block as json, so we filter
                    # against json values
                    value = field.get_json_value(value)

            return value

        if field.key not in ['date', 'item', 'items', 'string', 'text', 'bool', 'email', 'numeric']:
            return convert_value(value=value, field=field)

        if op in ['in', 'not_in', 'between'] and field.key != 'items':
            if isinstance(value, (tuple, list)):
                new_value = list(value)[:]
            else:
                new_value = [
                    convert_value(value=x.strip(), field=field) for x in str(value).split('|') if x.strip()
                ]
            if op == 'between' and len(new_value) != 2:
                if self._report_error_type != 'session-error':
                    # don't report error for management view
                    self.report_error(
                        _('Invalid value "%(value)s" for operator "%(operator)s" and filter "%(filter)s"')
                        % {
                            'value': value,
                            'operator': self.get_operator_name(op),
                            'filter': pending_attr,
                        },
                    )
                raise ValueError
            value = new_value
        else:
            value = convert_value(value=value, field=field)

        from wcs.backoffice.filter_fields import is_unary_operator

        if (
            not is_unary_operator(op)
            and op not in ['in', 'not_in', 'between']
            and field.key == 'items'
            and value
            and len(value) == 1
        ):
            # items field, if only one value operators are allowed, we need a single value
            value = value[0]
        if field.key in ['string', 'item', 'items']:
            if isinstance(value, list):
                value = [check_int(v) for v in value]
                # make sure all elements are of the same type
                if not all(isinstance(v, int) for v in value):
                    value = [str(v) for v in value]
            else:
                value = check_int(value)

        return value

    def get_criteria_from_operator(self, op, value, field_id, field=None):
        operators_mapping = {
            'eq': Equal,
            'ne': NotEqual,
            'lt': Less,
            'lte': LessOrEqual,
            'gt': Greater,
            'gte': GreaterOrEqual,
            'in': Contains,
            'icontains': ILike,
            'ieq': IEqual,
        }

        if isinstance(value, list) and op in ['eq', 'ne']:
            # items field, with a list of values: use in and not_in operators
            op = 'in' if op == 'eq' else 'not_in'

        if op == 'not_in':
            return Not(self.get_criteria_from_operator(op='in', value=value, field_id=field_id, field=field))
        if op == 'existing':
            return Not(
                self.get_criteria_from_operator(op='absent', value=value, field_id=field_id, field=field)
            )
        if op == 'is_today':
            return Equal(field_id, value=now().date(), field=field)
        if op == 'is_tomorrow':
            return Equal(field_id, value=now().date() + datetime.timedelta(days=1), field=field)
        if op == 'is_yesterday':
            return Equal(field_id, value=now().date() - datetime.timedelta(days=1), field=field)
        if op == 'is_this_week':
            monday = now().date()
            monday -= datetime.timedelta(days=monday.weekday())
            return Between(field_id, value=[monday, monday + datetime.timedelta(days=7)], field=field)
        if op == 'is_future':
            return Greater(field_id, value=now().date(), field=field)
        if op == 'is_past':
            return Less(field_id, value=now().date(), field=field)
        if op == 'is_today_or_future':
            return GreaterOrEqual(field_id, value=now().date(), field=field)
        if op == 'is_today_or_past':
            return LessOrEqual(field_id, value=now().date(), field=field)

        if op in operators_mapping:
            return operators_mapping[op](field_id, value, field=field)
        if op == 'absent':
            criterias = []
            if field:
                if not getattr(field, 'block_field', None):
                    criterias.append(Null(field_id, field=field))
                    if field.key == 'items':
                        criterias.append(Equal(field_id, [], field=field))
                    elif field.key not in ['bool', 'date', 'numeric']:
                        criterias.append(Equal(field_id, '', field=field))
                else:
                    criterias.append(BlockAbsent(field_id, field=field))
            return Or(criterias)
        if op == 'between':
            if not len(value) == 2:
                return Nothing()
            min_value, max_value = value[0], value[1]
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            return Between(field_id, [min_value, max_value], field=field)

    def get_operator_name(self, op):
        operator_names_mapping = {
            'eq': 'equal',
            'ne': 'not_equal',
            'lt': 'less_than',
            'lte': 'less_than_or_equal',
            'gt': 'greater_than',
            'gte': 'greater_than_or_equal',
        }
        return operator_names_mapping.get(op) or op

    def apply_filter_value(self, value, exclude=False):
        if not hasattr(self, 'pending_attr'):
            self.report_error(_('|filter_value called without |filter_by'))
            return self.none()
        if not self.pending_attr:
            self.report_error(_('|filter_value called without attribute (check |filter_by parameter)'))
            return self.none()
        op = 'ne' if exclude else getattr(self, 'pending_op', 'eq')
        if self.pending_attr in ['status', 'user', 'internal_id', 'number', 'distance', 'identifier']:
            return getattr(self, 'filter_by_%s' % self.pending_attr)(value, op)

        fields = list(self.get_fields(self.pending_attr))
        if not fields:
            self.report_error(_('Invalid filter "%s"') % self.pending_attr)
            return self.none()

        # check operator
        for field in fields:
            if field.key not in [
                'date',
                'item',
                'items',
                'string',
                'text',
                'bool',
                'email',
                'numeric',
                'file',
            ]:
                continue
            operators = self.get_field_allowed_operators(field) or []
            if op not in [o[0] for o in operators]:
                self.report_error(
                    _('Invalid operator "%(operator)s" for filter "%(filter)s"')
                    % {'operator': self.get_operator_name(op), 'filter': self.pending_attr},
                )
                return self.none()

        if value is not None:
            try:
                # consider all fields with same varname are of the same type
                # (it should definitely be)
                value = self.format_value(op=op, value=value, field=fields[0])
            except ValueError:
                return self.none()

        from wcs import sql

        # build criterias
        criterias = []
        for field in fields:
            field_id = sql.get_field_id(field)
            if value is None:
                if op not in ['eq', 'ne']:
                    # None value with comparison operator (le, lte, gt, gte, in, not_in, between ...)
                    return self.none()
                if hasattr(field, 'block_field'):
                    # no None values in block field data, apply absent/existing filters
                    if exclude:
                        criteria = self.get_criteria_from_operator(
                            op='existing', value=value, field_id=field_id, field=field
                        )
                    else:
                        criteria = self.get_criteria_from_operator(
                            op='absent', value=value, field_id=field_id, field=field
                        )
                else:
                    criteria_class = NotNull if exclude else Null
                    criteria = criteria_class(field_id)
            elif field.key not in [
                'date',
                'item',
                'items',
                'string',
                'text',
                'bool',
                'email',
                'numeric',
                'file',
            ]:
                criteria_class = NotEqual if exclude else Equal
                criteria = criteria_class(field_id, value, field=field)
            else:
                criteria = self.get_criteria_from_operator(op=op, value=value, field_id=field_id, field=field)
            criterias.append(criteria)

        if len(criterias) > 1:
            if exclude:
                criterias = [And(criterias)]
            else:
                criterias = [Or(criterias)]

        return self._clone(self._criterias + criterias)

    def apply_exclude_value(self, value):
        if hasattr(self, 'pending_op'):
            self.report_error(
                _('Operator filter is not allowed for exclude_value filter'),
            )
            return self.none()
        return self.apply_filter_value(value, exclude=True)

    def apply_op(self, op):
        from wcs.backoffice.filter_fields import is_unary_operator

        self.pending_op = op
        if is_unary_operator(op):
            return self.apply_filter_value(Ellipsis)
        return self

    def apply_eq(self):
        return self.apply_op('eq')

    def apply_ieq(self):
        return self.apply_op('ieq')

    def apply_ne(self):
        return self.apply_op('ne')

    def apply_lt(self):
        return self.apply_op('lt')

    def apply_lte(self):
        return self.apply_op('lte')

    def apply_gt(self):
        return self.apply_op('gt')

    def apply_gte(self):
        return self.apply_op('gte')

    def apply_in(self):
        return self.apply_op('in')

    def apply_not_in(self):
        return self.apply_op('not_in')

    def apply_absent(self):
        return self.apply_op('absent')

    def apply_existing(self):
        return self.apply_op('existing')

    def apply_between(self):
        return self.apply_op('between')

    def apply_icontains(self):
        return self.apply_op('icontains')

    def apply_is_today(self):
        return self.apply_op('is_today')

    def apply_is_tomorrow(self):
        return self.apply_op('is_tomorrow')

    def apply_is_yesterday(self):
        return self.apply_op('is_yesterday')

    def apply_is_this_week(self):
        return self.apply_op('is_this_week')

    def apply_is_future(self):
        return self.apply_op('is_future')

    def apply_is_past(self):
        return self.apply_op('is_past')

    def apply_is_today_or_future(self):
        return self.apply_op('is_today_or_future')

    def apply_is_today_or_past(self):
        return self.apply_op('is_today_or_past')

    def getlist(self, key):
        return LazyList(self, key)

    def getlistdict(self, keys):
        results = []
        for lazy_formdata in self:
            result = {}
            for key in keys:
                value = lazy_formdata.get(key)
                if hasattr(value, 'timetuple'):
                    value = value.timetuple()
                elif hasattr(value, 'get_value'):
                    value = value.get_value()
                result[key] = value
            results.append(result)
        return results

    def get_limit_offset_kwargs(self):
        offset, limit = 0, None
        if self._slice:
            start, stop = self._slice.start, self._slice.stop
            if start is not None:
                offset = start
                if offset < 0:
                    offset = self.total_count() + offset
            if stop is not None:
                if stop >= 0:
                    limit = stop - offset
                else:
                    limit = self.total_count() + stop - offset
        elif self._limit:
            limit = self._limit
        return {'offset': offset, 'limit': limit}

    def _populate_cache(self):
        if self._cached_resultset is not None:
            return
        formdef_slug = self._formdef and self._formdef.slug
        add_timing_mark(f'populate cache {formdef_slug} {self._criterias}')
        result = self._formdef.data_class().select_iterator(
            clause=self._criterias,
            order_by=self._order_by,
            itersize=200,
            **self.get_limit_offset_kwargs(),
        )
        self._cached_resultset = [LazyFormData(x) for x in result]

    def __getattr__(self, attribute):
        if attribute.startswith('count_status_'):
            # backward compatibility
            status = attribute[len('count_status_') :]
            return len(self._formdef.data_class().get_ids_with_indexed_value('status', 'wf-%s' % status))
        if attribute.startswith('filter_by_'):
            attribute_name = attribute[len('filter_by_') :]
            return lambda: self.filter_by(attribute_name)
        if attribute == 'formdef':
            warnings.warn('Deprecated access to formdef', DeprecationWarning)
            return self._formdef
        raise AttributeError(attribute)

    def __len__(self):
        if self._cached_resultset is not None:
            return len(self._cached_resultset)
        return self.count

    def __getitem__(self, key):
        if self._cached_resultset is None and not self._limit and not self._slice and is_supported_slice(key):
            return self._clone(criterias=self._criterias, slice=key)
        try:
            if not isinstance(key, slice):
                int(key)
        except ValueError:
            # A django template doing formdef.objects.drafts would start by
            # doing ['drafts'], that would raise TypeError and then continue
            # to accessing .drafts (this is done in _resolve_lookup).
            # We need to abort earlier as we don't want to load all formdata
            # in that situation.
            raise TypeError
        self._populate_cache()
        return self._cached_resultset[key]

    def __iter__(self):
        self._populate_cache()
        yield from self._cached_resultset

    def __nonzero__(self):
        return any(self)


class LazyList:
    def __init__(self, lazy_manager, key, slice=None):
        self._lazy_manager = lazy_manager
        self._key = key
        self._cached_resultset = None
        if slice is not None:
            self._lazy_manager._slice = slice

    def _populate_cache(self):
        if self._cached_resultset is not None:
            return
        self._cached_resultset = []
        for lazy_formdata in self._lazy_manager:
            value = lazy_formdata.get(self._key)
            if value is not None:
                if hasattr(value, 'timetuple'):
                    value = value.timetuple()
                elif hasattr(value, 'get_value'):
                    value = value.get_value()
            self._cached_resultset.append(value)

    def __len__(self):
        if self._cached_resultset is not None:
            return len(self._cached_resultset)
        return len(self._lazy_manager)

    def __repr__(self):
        return '<LazyList, %s%s %s, %s %s>' % (
            self._lazy_manager._formdef.verbose_name,
            _(':'),
            self._lazy_manager._formdef.name,
            _('attribute:'),
            self._key,
        )

    def __iter__(self):
        self._populate_cache()
        yield from self._cached_resultset

    def __nonzero__(self):
        return any(self)

    def __contains__(self, value):
        if self._cached_resultset is not None:
            field = self._lazy_manager.get_field(self._key)
            if field is not None:
                try:
                    value = field.convert_value_from_anything(value)
                except (ValueError, AttributeError):
                    pass
            return value in list(self)

        queryset = list(self._lazy_manager.filter_by(self._key).apply_filter_value(value).limit(1))
        return len(queryset) == 1

    def __eq__(self, other):
        return list(self) == list(other)

    def __getitem__(self, key):
        if is_supported_slice(key) and not self._lazy_manager._slice:
            return LazyList(self._lazy_manager, self._key, slice=key)
        return list(self)[key]

    def get_value(self):
        # unlazy operation
        return list(self)


class LazyFormDef:
    def __init__(self, formdef):
        self._formdef = formdef

    @property
    def name(self):
        return self._formdef.name

    @property
    def slug(self):
        return self._formdef.url_name

    @property
    def class_name(self):
        # reserved for logged errors
        return self._formdef.__class__.__name__

    @property
    def objects(self):
        return LazyFormDefObjectsManager(self._formdef)

    @property
    def option(self):
        return LazyFormDefOptions(self._formdef)

    @property
    def type(self):
        return self._formdef.xml_root_node

    @property
    def backoffice_submission_url(self):
        return self._formdef.get_backoffice_submission_url()

    @property
    def frontoffice_submission_url(self):
        return self._formdef.get_url()

    @property
    def publication_disabled(self):
        return self._formdef.is_disabled()

    @property
    def publication_datetime(self):
        return self._formdef.publication_datetime

    @property
    def publication_expiration_datetime(self):
        return self._formdef.expiration_datetime


class LazyFormData(LazyFormDef):
    # noqa pylint: disable=too-many-public-methods

    def __init__(self, formdata):
        super().__init__(formdata.formdef)
        self._formdata = formdata

    def inspect_keys(self):
        hidden_keys = {'field', 'inspect_keys', 'page_no', 'formdef', 'objects'}
        if self.type != 'formdef' or not self._formdata.id:
            hidden_keys.add('short_url')
        for key in dir(self):
            if key[0] == '_' or key in hidden_keys:
                continue
            if key == 'parent':
                if self.parent:  # hide parent when it's None
                    yield key, False  # = do not recurse
            else:
                yield key

    @property
    def objects(self):
        return LazyFormDefObjectsManager(self._formdef, formdata=self._formdata)

    @property
    def formdef(self):
        return LazyFormDef(self._formdata.formdef)

    @property
    def internal_id(self):
        if hasattr(self._formdata, '_edited_id'):
            # when a formdata is being edited the transient object keeps
            # the original id in this attribute.
            return self._formdata._edited_id
        return self._formdata.id

    @property
    def receipt_date(self):
        if not self._formdata.receipt_time:
            return ''
        receipt_time = make_datetime(self._formdata.receipt_time)
        receipt_time = make_naive(receipt_time) if is_aware(receipt_time) else receipt_time
        return formats.date_format(receipt_time)

    @property
    def receipt_time(self):
        if not self._formdata.receipt_time:
            return ''
        receipt_time = make_datetime(self._formdata.receipt_time)
        receipt_time = make_naive(receipt_time) if is_aware(receipt_time) else receipt_time
        return formats.time_format(receipt_time)

    @property
    def identifier(self):
        return self._formdata.identifier

    @property
    def uuid(self):
        return self._formdata.uuid

    @property
    def number(self):
        return self._formdata.get_display_id()

    @property
    def number_raw(self):
        return str(self._formdata.id) if self._formdata.id else None

    @property
    def url(self):
        return self._formdata.get_url()

    @property
    def url_backoffice(self):
        return self._formdata.get_url(backoffice=True)

    @property
    def backoffice_url(self):
        return self._formdata.get_url(backoffice=True)

    @property
    def api_url(self):
        return self._formdata.get_api_url()

    @property
    def short_url(self):
        return self._formdata.get_short_url()

    @property
    def uri(self):
        return '%s/%s/' % (self._formdef.url_name, self._formdata.id)

    @property
    def criticality_level(self):
        return self._formdata.criticality_level

    @property
    def criticality_label(self):
        try:
            return self._formdata.get_criticality_level_object().name
        except IndexError:
            return None

    @property
    def digest(self):
        return self._formdata.default_digest

    @property
    def display_name(self):
        return self._formdata.get_display_name()

    @property
    def receipt_datetime(self):
        return make_datetime(self._formdata.receipt_time) if self._formdata.receipt_time else None

    @property
    def last_update_datetime(self):
        last_update_time = self._formdata.last_update_time
        return make_datetime(last_update_time) if last_update_time else None

    @property
    def status(self):
        return self._formdata.get_status_label()

    @property
    def status_is_endpoint(self):
        return self._formdata.is_at_endpoint_status()

    @property
    def tracking_code(self):
        formdata = self._formdata
        if not formdata.status and formdata.data:
            if 'future_tracking_code' in formdata.data:
                return formdata.data['future_tracking_code']
            if 'draft_formdata_id' in formdata.data:
                formdata = formdata.formdef.data_class().get(formdata.data['draft_formdata_id'])
        return formdata.tracking_code

    @property
    def submission_backoffice(self):
        return self._formdata.backoffice_submission

    @property
    def submission_channel(self):
        return self._formdata.submission_channel

    @property
    def submission_channel_label(self):
        return self._formdata.get_submission_channel_label()

    @property
    def submission_agent(self):
        try:
            return LazyUser(get_publisher().user_class.get(self._formdata.submission_agent_id))
        except (TypeError, KeyError):
            return None

    @property
    def submission_context(self):
        return self._formdata.submission_context

    @property
    def status_url(self):
        if not self._formdata.id:
            return ''
        return '%sstatus' % self._formdata.get_url()

    @property
    def details(self):
        return self._formdata.get_form_details()

    _cached_user = Ellipsis

    @property
    def user(self):
        if self._cached_user is Ellipsis:
            user = self._formdata.get_user()
            self._cached_user = LazyUser(user) if user else None
        return self._cached_user

    @property
    def var(self):
        return LazyFormDataVar(self._formdef.get_all_fields(), self._formdata.data, self._formdata)

    @property
    def field(self):
        # no lazy dictionary here as it's legacy.
        d = {}
        for field in self._formdef.get_all_fields():
            if not hasattr(field, 'get_view_value'):
                continue
            value = self._formdata.data.get(field.id)
            if value is not None and field.convert_value_to_str:
                value = field.convert_value_to_str(value)
            elif value is None:
                value = ''
            identifier_name = misc.simplify(field.label, space='_')
            d[identifier_name] = value

        return d

    @property
    def role(self):
        workflow_roles = {}
        if self._formdef.workflow_roles:
            workflow_roles.update(self._formdef.workflow_roles)
        if self._formdata.workflow_roles:
            workflow_roles.update(self._formdata.workflow_roles)

        return get_workflow_roles_substitution_variables(workflow_roles)

    @property
    def comment(self):
        if self._formdata.evolution:
            latest_evolution = self._formdata.evolution[-1]
            return latest_evolution.get_plain_text_comment() or latest_evolution.comment or ''
        return ''

    @property
    def page_no(self):
        return int(self._formdata.page_no)

    @property
    def attachments(self):
        from wcs.workflows import AttachmentsSubstitutionProxy

        return AttachmentsSubstitutionProxy(self._formdata)

    @property
    def geoloc(self):
        data = {}
        if self._formdata.geolocations:
            for k, v in self._formdata.geolocations.items():
                data[k] = v
                data[k + '_lat'] = v.get('lat')
                data[k + '_lon'] = v.get('lon')
        return data

    @property
    def distance(self):
        return getattr(self._formdata, '_distance', None)

    @property
    def previous_status(self):
        if getattr(self._formdata, '_previous_status', None):
            # for minimal formdata object used during edition.
            return self._formdata._previous_status
        if self._formdata.evolution:
            for evolution in reversed(self._formdata.evolution):
                if evolution.status and evolution.status != self._formdata.status:
                    return self._formdata.get_status_label(evolution.status)
        return ''

    @property
    def status_changed(self):
        first_evolution_in_current_status = None
        for evolution in reversed(self._formdata.evolution or []):
            if evolution.status and evolution.status != self._formdata.status:
                break
            if evolution.status:
                first_evolution_in_current_status = evolution

        return bool(
            self.status != self.previous_status
            and self._formdata.evolution
            and self._formdata.evolution[-1].status
            and first_evolution_in_current_status is self._formdata.evolution[-1]
            and not self._formdata.evolution[-1].last_jump_datetime
        )

    @property
    def evolution(self):
        return self._formdef.get_detailed_evolution(self._formdata)

    @property
    def links(self):
        from .wf.create_formdata import LazyFormDataLinks

        return LazyFormDataLinks(self._formdata)

    @property
    def reverse_links(self):
        return LazyFormDataReverseLinks(self._formdata)

    @property
    def workflow_email(self):
        # form_ workflow_email_ <slug (action varname)> _ <index> _ etc.
        # ex: form_email_xxx_2_addresses
        from .wf.sendmail import LazyFormDataEmailsBase

        return LazyFormDataEmailsBase(self._formdata)

    @property
    def workflow_form(self):
        # form_ workflow_form_ <slug (action varname)> _ <index> _var_ etc.
        # ex: form_workflow_form_xxx_2_var_file
        # (index can be "latest")
        from .wf.form import LazyFormDataWorkflowForms

        return LazyFormDataWorkflowForms(self._formdata)

    @property
    def workflow_wscall(self):
        # form_ workflow_wscall_ <slug (action varname)> _ <index> _ etc.
        # ex: form_workflow_form_wscall_2_status
        from .wf.wscall import LazyFormDataWsCallsBase

        return LazyFormDataWsCallsBase(self._formdata)

    @property
    def trigger(self):
        # form_ trigger_ <slug (trigger name)> _ <index> _content_ etc.
        # ex: form_trigger_paid_content_XXX
        # (index can be "latest")
        from .wf.jump import LazyFormDataWorkflowTriggers

        return LazyFormDataWorkflowTriggers(self._formdata)

    @property
    def parent(self):
        formdata = self._formdata.get_parent()
        if formdata is None:
            return None
        return formdata.get_substitution_variables()

    @property
    def workflow_data(self):
        data = self._formdata.workflow_data or {}
        return {k: data[k] for k in data if not k.startswith('_')}

    @property
    def jumps(self):
        from wcs.workflows import JumpEvolutionPart

        jump_parts = []
        for part in self._formdata.iter_evolution_parts(klass=JumpEvolutionPart):
            jump_parts.append(part.identifier)
        return jump_parts

    @property
    def latest_jump(self):
        from wcs.workflows import JumpEvolutionPart

        for part in self._formdata.iter_evolution_parts(klass=JumpEvolutionPart, reverse=True):
            return part.identifier
        return ''

    def export_to_json(self, include_files=True):
        # this gets used to generate an email attachment :/
        return self._formdata.export_to_json(include_files=include_files)

    def get(self, key):
        # compatibility with |get filter, to return a field by varname
        try:
            return getattr(self.var, key)
        except AttributeError:
            # fallback to CompatibilityNamesDict, this allows filters to do
            # queryset|first|get:"form_var_plop"
            compat_dict = CompatibilityNamesDict({'form': self})
            return compat_dict.get(key)
        except Exception as e:
            get_publisher().record_error(_('|get called with invalid key (%s)') % key, exception=e)
            return None

    def __getitem__(self, key):
        if isinstance(key, int):
            raise TypeError('invalid integer getitem on object')
        try:
            return getattr(self, str(key))
        except AttributeError:
            if isinstance(key, str) and key.startswith('f'):
                for field in self._formdef.get_all_fields():
                    if str(field.id).replace('-', '_') == str(key[1:]):
                        return self._formdata.data.get(field.id)
            raise


class LazyFormDataReverseLinks:
    inspect_collapse = True

    def __init__(self, formdata):
        self._formdata = formdata

    _relations = None

    @property
    def relations(self):
        if self._relations is not None:
            return self._relations
        self._relations = {}
        for relation in self._formdata.formdef.reverse_relations:
            if not relation['varname']:
                continue
            key = '%s_%s' % (relation['obj'], relation['varname'])
            key = key.replace(':', '_').replace('-', '_')
            self._relations[key] = relation
        return self._relations

    def inspect_keys(self):
        return self.relations.keys()

    def __getitem__(self, key):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        if not isinstance(key, str):
            raise KeyError(str(key))

        relation = self.relations[key]

        formdef_type, formdef_slug = relation['obj'].split(':')
        formdef_class = FormDef
        if formdef_type == 'carddef':
            formdef_class = CardDef
        formdef = formdef_class.get_by_slug(formdef_slug, ignore_errors=True)
        if formdef is None:
            return None
        lazy_manager = LazyFormDefObjectsManager(formdef=formdef)
        formdatas = [
            LazyFormDataReverseLinksItem(formdata)
            for formdata in lazy_manager.filter_by(relation['varname']).apply_filter_value(
                str(self._formdata.get_natural_key())
            )
        ]
        if not formdatas:
            return []
        return LazyFormDataReverseLinksItems(formdatas)


class LazyFormDataReverseLinksItems:
    inspect_collapse = True

    def __init__(self, formdatas):
        self._formdatas = formdatas

    def inspect_keys(self):
        return [str(x) for x in range(len(self._formdatas))]

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            raise KeyError
        return self._formdatas[key]

    def __len__(self):
        return len(self._formdatas)

    def __iter__(self):
        yield from self._formdatas


class LazyFormDataReverseLinksItem:
    inspect_collapse = True

    def __init__(self, formdata):
        self._formdata = formdata

    def inspect_keys(self):
        return ['form']

    @property
    def form(self):
        return self._formdata

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            compat_dict = CompatibilityNamesDict({'form': self._formdata})
            return compat_dict[key]


class LazyFormDataVar:
    def __init__(self, fields, data, formdata=None, base_formdata=None):
        self._fields = fields
        self._data = data or {}
        self._formdata = formdata
        self._base_formdata = base_formdata

    def inspect_keys(self):
        return self.varnames.keys()

    _varnames = None

    @property
    def varnames(self):
        if self._varnames is not None:
            return self._varnames
        self._varnames = {}
        for field in self._fields:
            if field.is_no_data_field:
                continue
            if not field.varname or not CompatibilityNamesDict.valid_key_regex.match(field.varname):
                continue
            if field.varname in self._varnames:
                # duplicated varname
                value = self._data.get(self._varnames[field.varname].id)
                if value or value is False:
                    # previous field had a value (not None or the empty string),
                    # stay on it.
                    continue
                # else continue and update _varnames with new field reference.
            self._varnames[field.varname] = field
        return self._varnames

    def get_field_kwargs(self, field):
        return {
            'data': self._data,
            'field': field,
            'formdata': self._formdata,
            'base_formdata': self._base_formdata,
        }

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(str(key))
        try:
            field = self.varnames[key]
        except KeyError:
            # key was unknown but for some  values we may still have to provide
            # multiple keys (for example file fields will expect to have both
            # form_var_foo and form_var_foo_raw set to None) and user
            # conditions may use the "is" operator that cannot be overridden
            # (this applies to None as well as boolean values).
            #
            # Therefore we catch unknown keys with known suffixes ("foo_raw")
            # and remove the suffix to get the actual field.  If the data is
            # None or a boolean type, we return it as is.
            if not (key.endswith('_raw') or key.endswith('_url')):
                raise
            maybe_varname = key.rsplit('_', 1)[0]
            field = self.varnames[maybe_varname]
            if key.endswith('_raw') and field.key == 'bool':
                # turn None into False so boolean fields are always a boolean.
                return bool(self._data.get(field.id))

            if self._data.get(field.id) in (None, True, False):
                # valid suffix and data of the correct type
                return self._data.get(field.id)
            raise KeyError(key)

        # let boolean pass through, to get None handled as False
        if field.key != 'bool':
            if self._data.get(field.id) is None:
                return NoneFieldVar(**self.get_field_kwargs(field))

            if str(field.id) not in self._data:
                raise KeyError(key)

        klass = LazyFieldVar
        if field.store_structured_value:
            klass = LazyFieldVarStructured
        klass = {  # custom types
            'date': LazyFieldVarDate,
            'map': LazyFieldVarMap,
            'password': LazyFieldVarPassword,
            'file': LazyFieldVarFile,
            'block': LazyFieldVarBlock,
            'bool': LazyFieldVarBool,
            'computed': LazyFieldVarComputed,
            'items': LazyFieldVarItems,
            'numeric': LazyFieldVarNumeric,
            'time-range': LazyFieldVarTimeRange,
        }.get(field.key, klass)

        return klass(**self.get_field_kwargs(field))

    def __getattr__(self, attr):
        try:
            return self.__getitem__(attr)
        except KeyError:
            raise AttributeError(attr)


class LazyFieldVar:
    def __init__(self, data, field, formdata=None, base_formdata=None, **kwargs):
        self._data = data
        self._field = field
        self._formdata = formdata
        self._base_formdata = base_formdata
        self._field_kwargs = kwargs

    @property
    def raw(self):
        if self._field.store_display_value or self._field.key in ('file', 'date'):
            return self._data.get(self._field.id)
        raise AttributeError('raw')

    def get_value(self):
        if self._field.store_display_value:
            return self._data.get('%s_display' % self._field.id)
        value = self._data.get(self._field.id)
        if self._field.convert_value_to_str:
            return self._field.convert_value_to_str(value)
        return value

    def __str__(self):
        return force_str(self.get_value())

    def __nonzero__(self):
        if self._field.key == 'bool':
            return bool(self._data.get(self._field.id))
        return bool(self.get_value())

    __bool__ = __nonzero__

    def __contains__(self, value):
        return value in self.get_value()

    def __eq__(self, other):
        return force_str(self) == force_str(other)

    def __ne__(self, other):
        return force_str(self) != force_str(other)

    def __lt__(self, other):
        return force_str(self) < force_str(other)

    def __le__(self, other):
        return force_str(self) <= force_str(other)

    def __gt__(self, other):
        return force_str(self) > force_str(other)

    def __ge__(self, other):
        return force_str(self) >= force_str(other)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self.get_value()[key]
        try:
            return getattr(self, key)
        except AttributeError:
            pass
        raise KeyError(key)

    def __iter__(self):
        return iter(self.get_value() or [])

    def __add__(self, other):
        return self.get_value().__add__(other)

    def __radd__(self, other):
        return other + self.get_value()

    def __mul__(self, other):
        return self.get_value().__mul__(other)

    def __len__(self):
        return len(self.get_value())

    def __int__(self):
        return int(self.get_value())

    def startswith(self, other):
        return self.get_value().startswith(other)

    def strip(self, *args):
        warnings.warn('Deprecated use of .strip method', DeprecationWarning)
        return self.get_value().strip(*args)

    def split(self, *args, **kwargs):
        # Compatibility with usage of variable as a string. It is
        # recommended to use appropriate properties instead.
        return str(self).split(*args, **kwargs)

    def __getstate__(self):
        raise AssertionError('lazy cannot be pickled')


class NoneFieldVar(LazyFieldVar):
    def get_value(self):
        return None

    def __len__(self):
        return 0

    def getlist(self, key):
        return []


class LazyFieldVarComplex(LazyFieldVar):
    def has_live_data_source(self):
        real_data_source = self._field.get_real_data_source()
        if not real_data_source:
            return False
        if real_data_source.get('type', '') == 'wcs:users':
            return True
        if real_data_source.get('type', '').startswith('carddef:'):
            return True
        return False

    def inspect_keys(self):
        keys = []
        structured_value = self.get_field_var_value()

        def walk(base, value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if CompatibilityNamesDict.valid_key_regex.match(k):
                        walk(k if not base else base + '_' + k, v)
            else:
                keys.append(base)

        if isinstance(structured_value, list):
            for i, value in enumerate(structured_value):
                walk(str(i), value)
        elif isinstance(structured_value, dict):
            walk('', structured_value)

        return list(set(keys))

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            pass
        structured_value = self.get_field_var_value()
        if not structured_value:
            raise KeyError(key)
        if isinstance(structured_value, dict):
            return structured_value[key]
        if isinstance(structured_value, list):
            for i, struct_value in enumerate(structured_value):
                if str(key) == str(i):
                    return struct_value
        raise KeyError(key)


class LazyFieldVarLiveCardMixin:
    def get_data_id(self):
        return self._data.get(self._field.id)

    @property
    def live(self):
        real_data_source = self._field.get_real_data_source()
        if not (
            real_data_source
            and (
                real_data_source.get('type', '').startswith('carddef:')
                or real_data_source.get('type', '') == 'wcs:users'
            )
        ):
            raise AttributeError('live')
        if real_data_source.get('type', '') == 'wcs:users':
            try:
                return LazyUser(get_publisher().user_class.get(self.get_data_id()))
            except KeyError:
                return None
        request = get_request()
        card_id = self.get_data_id()
        if request:
            # cache during request
            cache_key = '%s-%s' % (self._field.data_source['type'], card_id)
            if not hasattr(request, 'live_card_cache'):
                request.live_card_cache = {}
            else:
                carddata = request.live_card_cache.get(cache_key, Ellipsis)
                if carddata is None:
                    return None
                if carddata is not Ellipsis:
                    # cached data
                    return LazyFormData(carddata)
        from wcs.carddef import CardDef

        try:
            carddef = CardDef.get_by_urlname(self._field.data_source['type'].split(':')[1], use_cache=True)
            carddata = carddef.data_class().get_by_id(card_id)
            if request:
                request.live_card_cache[cache_key] = carddata
        except KeyError:
            if request:
                request.live_card_cache[cache_key] = None
            return None
        return LazyFormData(carddata)


class LazyFieldVarComputed(LazyFieldVarComplex, LazyFieldVarLiveCardMixin):
    def inspect_keys(self):
        keys = super().inspect_keys()
        try:
            self.live
        except AttributeError:
            pass  # don't advertise if there's no value behind
        else:
            keys.append('live')
        return keys

    def get_field_var_value(self):
        return self.get_value()


class LazyFieldVarStructured(LazyFieldVarComplex, LazyFieldVarLiveCardMixin):
    def inspect_keys(self):
        if not self._data.get(self._field.id):
            return []
        real_data_source = self._field.get_real_data_source()
        if (
            real_data_source
            and self._field.key in ('item', 'items')
            and real_data_source.get('type', '') == 'wcs:users'
        ):
            return ['raw', 'live']

        keys = ['raw']
        structured_value = self._field.get_structured_value(self._data)
        if structured_value:
            keys.append('structured')

        if (
            real_data_source
            and self._field.key in ('item', 'items')
            and real_data_source.get('type', '').startswith('carddef:')
        ):
            try:
                self.live
            except (AttributeError, KeyError):
                # don't advertise "live" if linked data is missing
                pass
            else:
                keys.append('live')

        keys.extend(super().inspect_keys())

        return keys

    @property
    def structured_raw(self):
        # backward compatibility, _structured should be use.
        return self._field.get_structured_value(self._data)

    @property
    def structured(self):
        return self._field.get_structured_value(self._data)

    def get_field_var_value(self):
        return self.structured


class DateOperatorsMixin:
    def __eq__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) == parse_datetime(other)

    def __ne__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) != parse_datetime(other)

    def __gt__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) > parse_datetime(other)

    def __lt__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) < parse_datetime(other)

    def __ge__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) >= parse_datetime(other)

    def __le__(self, other):
        if hasattr(other, 'timetuple'):
            other = other.timetuple()
        elif hasattr(other, 'get_value'):
            other = other.get_value()
        return parse_datetime(self.timetuple()) <= parse_datetime(other)

    def __hash__(self):
        return super().__hash__()


class FlexibleDateObject(DateOperatorsMixin, datetime.date):
    pass


class FlexibleDatetimeObject(DateOperatorsMixin, datetime.datetime):
    pass


def flexible_date(value):
    if isinstance(value, datetime.datetime):
        return FlexibleDatetimeObject.fromtimestamp(time.mktime(value.timetuple()))
    return FlexibleDateObject(value.year, value.month, value.day)


class LazyFieldVarDate(DateOperatorsMixin, LazyFieldVar):
    def inspect_keys(self):
        return ['year', 'month', 'day']

    def get_raw(self):
        return self._data.get(self._field.id)

    def timetuple(self):
        return self.get_raw()

    # for backward compatibility with sites using time.struct_time
    # methods we still have to provide time.struct_time properties.
    @property
    def tm_year(self):
        return self.get_raw().tm_year

    @property
    def tm_mon(self):
        return self.get_raw().tm_mon

    @property
    def tm_mday(self):
        return self.get_raw().tm_mday

    @property
    def tm_hour(self):
        return self.get_raw().tm_hour

    @property
    def tm_min(self):
        return self.get_raw().tm_min

    @property
    def tm_sec(self):
        return self.get_raw().tm_sec

    @property
    def tm_wday(self):
        return self.get_raw().tm_wday

    @property
    def tm_yday(self):
        return self.get_raw().tm_yday

    year = tm_year
    month = tm_mon
    day = tm_mday


class LazyFieldVarMap(LazyFieldVarStructured):
    def inspect_keys(self):
        return ['lat', 'lon', 'reverse'] if self.get_field_var_value() else []

    @property
    def reverse(self):
        return LazyFieldVarMapReverse(lat=self['lat'], lon=self['lon'])

    def __str__(self):
        # backward compatibility
        value = self._data.get(self._field.id)
        if not value:
            return ''
        return '%(lat)s;%(lon)s' % value


class LazyFieldVarMapReverse:
    inspect_collapse = True

    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon

    def inspect_keys(self):
        return ['address']

    @property
    def address(self):
        data = misc.get_reverse_geocoding_data(self.lat, self.lon)
        return (json.loads(data) or {}).get('address')


class LazyFieldVarLiveSequenceItem(LazyFieldVarLiveCardMixin):
    def __init__(self, field, data, idx):
        self._field = field
        self._data = data
        self.idx = idx

    def get_data_id(self):
        return self._data.get(self._field.id)[self.idx]


class LazyFieldVarLiveSequence:
    def __init__(self, field, data):
        self._field = field
        self._data = data
        self._list = data.get(self._field.id) or []

    def inspect_keys(self):
        # do not advertise index of unavailable items
        for i in range(len(self)):  # noqa pylint: disable=consider-using-enumerate
            try:
                self[i]
            except KeyError:
                pass  # removed item
            else:
                yield str(i)

    def __len__(self):
        return len(self._list)

    def __iter__(self):
        yield from self._list

    def getlist(self, key):
        result = []
        for i in range(len(self._list)):
            try:
                item = LazyFieldVarLiveSequenceItem(self._field, self._data, i).live
            except (AttributeError, IndexError):
                value = None
            else:
                value = CompatibilityNamesDict({'X': item}).get(f'X_{key}')
            result.append(value)
        return result

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            raise KeyError(key)
        try:
            return LazyFieldVarLiveSequenceItem(self._field, self._data, key).live
        except (AttributeError, IndexError):
            raise KeyError(key)


class LazyFieldVarItems(LazyFieldVarStructured):
    @property
    def live(self):
        return LazyFieldVarLiveSequence(self._field, self._data)

    def getlist(self, key):
        structured_value = self.structured
        if structured_value:
            return [str(x.get(key, '')) for x in structured_value or []]
        if key in ('id', 'text'):
            # simple source, where there's no structure value, just a simple
            # list of values that can be considered both id and text.
            return self.raw or []
        return []

    def getlistdict(self, keys):
        return [{key: str(x.get(key, '')) or None for key in keys} for x in self.structured or []]

    def __len__(self):
        return len(self.getlist('id'))

    def __contains__(self, value):
        return str(value) in self.getlist('id') or (self.structured and str(value) in self.getlist('text'))

    def get_iterable_value(self):
        try:
            return [self.live[i] for i in range(len(self.live))]
        except KeyError:
            return self.structured or self.raw


class LazyFieldVarBool(LazyFieldVar):
    def get_value(self):
        return bool(self._data.get(self._field.id))

    @property
    def raw(self):
        return self.get_value()


class LazyFieldVarNumeric(LazyFieldVar):
    def get_value(self):
        return self._data.get(self._field.id)

    def _comp(self, other, op):
        return op(misc.parse_decimal(self, do_raise=True), misc.parse_decimal(other, do_raise=True))

    __ne__ = functools.partialmethod(_comp, op=operator.ne)
    __lt__ = functools.partialmethod(_comp, op=operator.lt)
    __le__ = functools.partialmethod(_comp, op=operator.le)
    __gt__ = functools.partialmethod(_comp, op=operator.gt)
    __ge__ = functools.partialmethod(_comp, op=operator.ge)

    def __eq__(self, other):
        # accept comparing as string to match legacy behaviour
        return misc.unlazy(self) == misc.unlazy(other) or str(misc.unlazy(self)) == str(misc.unlazy(other))


class LazyFieldVarPassword(LazyFieldVar):
    def __getitem__(self, key):
        # get subpart (cleartext, md5, sha1) if it exists
        field_value = self._data.get(self._field.id)
        if key in field_value:
            return field_value[key]
        return super()._getitem__(key)


class LazyFieldVarFile(LazyFieldVar):
    def inspect_keys(self):
        keys = ['raw']
        # raw value should always have a get_fs_filename method, this protects against
        # invalid values.
        if hasattr(self.raw, 'get_fs_filename'):
            if hasattr(self._formdata, 'get_file_base_url') or self._base_formdata:
                keys.append('url')
            if self.raw.get_fs_filename():
                keys.append('file_size')
        return keys

    def get(self, key):  # for |getlist
        assert key in ('file_digest',)
        return getattr(self, key, None)

    @property
    def url(self):
        if 'url' not in self.inspect_keys():
            return None
        if self._base_formdata:
            return self._field.get_download_url(formdata=self._base_formdata, file_value=self.raw)
        return self._field.get_download_url(formdata=self._formdata, **self._field_kwargs)

    @property
    def file_digest(self):
        return self.raw.file_digest() if self.raw else None

    @property
    def file_size(self):
        return self.raw.get_file_size() if self.raw else None

    def __len__(self):
        return len(self.raw.base_filename) if self.raw and self.raw.base_filename else 0


class LazyBlockDataVar(LazyFormDataVar):
    def __init__(
        self, fields, data, formdata=None, parent_field=None, parent_field_index=0, base_formdata=None
    ):
        super().__init__(fields, data, formdata=formdata)
        self.parent_field = parent_field
        self.parent_field_index = parent_field_index
        self.base_formdata = base_formdata

    def get_field_kwargs(self, field):
        kwargs = super().get_field_kwargs(field)
        kwargs['parent_field'] = self.parent_field
        kwargs['parent_field_index'] = self.parent_field_index
        kwargs['base_formdata'] = self.base_formdata
        return kwargs


class LazyFieldVarBlock(LazyFieldVar):
    def inspect_keys(self):
        try:
            if self._field.max_items and int(self._field.max_items) <= 1:
                return ['var']
        except ValueError:
            pass
        data = self._formdata.data.get(self._field.id)['data']
        return [str(x) for x in range(len(data))]

    def get_value(self):
        return self._data.get(str(self._field.id))

    def get_iterable_value(self):
        return list(self)

    def __str__(self):
        return self._data.get('%s_display' % self._field.id) or '---'

    def __getitem__(self, key):
        if key == 'raw' and get_publisher().complex_data_cache is None:
            # internal detail, only allowed when using complex data
            # (in computed fields, create card actions, etc.)
            raise KeyError(key)
        try:
            int(key)
        except ValueError:
            return super().__getitem__(key)
        try:
            data = self._formdata.data.get(self._field.id)['data'][int(key)]
        except IndexError:
            raise KeyError(key)
        try:
            block_field = self._field.block
        except KeyError:
            # block was deleted, ignore
            return None
        return LazyBlockDataVar(
            block_field.fields,
            data,
            formdata=self._formdata,
            parent_field=self._field,
            parent_field_index=int(key),
            base_formdata=self._base_formdata,
        )

    def __len__(self):
        data = self._formdata.data.get(self._field.id)['data']
        return len(data)

    @property
    def var(self):
        # alias when there's a single item
        return self[0]

    def __iter__(self):
        data = self._formdata.data.get(self._field.id)['data']
        for i in range(len(data)):
            yield self[i]

    def getlist(self, key):
        # called by |getlist filter
        for field in self._field.block.fields:
            if field.varname == key:
                break
        else:
            try:
                value = [CompatibilityNamesDict({'X': x})[f'X_{key}'] for x in self]
            except KeyError:
                raise AttributeError(str(key))
            return value
        return [data.get(field.id) for data in self._formdata.data.get(self._field.id)['data']]

    def getlistdict(self, keys):
        fields = []
        for key in keys:
            matchings_fields = [field for field in self._field.block.fields if field.varname == key]
            matching_field_id = matchings_fields[0].id if matchings_fields else None
            fields.append((key, matching_field_id))

        return [
            {key: data.get(field_id) if field_id else None for key, field_id in fields}
            for data in self._formdata.data.get(self._field.id)['data']
        ]


class LazyFieldVarTimeRange(LazyFieldVarStructured):
    def inspect_keys(self):
        return ['start_datetime', 'end_datetime', 'api'] if self.get_field_var_value() else []


class LazyUser:
    def __init__(self, user):
        self._user = user

    def inspect_keys(self):
        return ['display_name', 'email', 'var', 'nameid', 'has_deleted_account']

    @property
    def display_name(self):
        return self._user.display_name

    @property
    def email(self):
        return self._user.email

    @property
    def has_deleted_account(self):
        return bool(self._user.deleted_timestamp)

    @property
    def var(self):
        return LazyFormDataVar(self._user.get_formdef().fields, self._user.form_data)

    @property
    def admin_access(self):
        return self._user.can_go_in_admin()

    @property
    def backoffice_access(self):
        return self._user.can_go_in_backoffice()

    @property
    def name_identifier(self):
        d = {}
        for i, name_identifier in enumerate(self._user.name_identifiers):
            d[str(i)] = name_identifier
        return d

    @property
    def nameid(self):
        return self._user.nameid

    def get_value(self):
        return self._user

    def __getitem__(self, key):
        return getattr(self, key)

    def __getattr__(self, attr):
        try:
            return super().__getattr__(attr)
        except AttributeError:
            return getattr(self._user, attr)


class LazyRequest:
    def __init__(self, request):
        self._request = request

    @property
    def quixote_request(self):  # compatibility
        return self._request

    @property
    def GET(self):
        return self._request.query_parameters_forced_value or self._request.django_request.GET

    @property
    def META(self):
        return self._request.django_request.META

    @property
    def is_in_backoffice(self):
        return self._request.is_in_backoffice()

    @property
    def is_from_mobile(self):
        return self._request.is_from_mobile()

    @property
    def method(self):
        return self._request.method

    @property
    def user(self):
        return self._request.user

    @property
    def view_name(self):
        return getattr(self._request, 'view_name', None)


class LazyFormDefOptions(LazyFormDataVar):
    def __init__(self, formdef):
        self._formdef = formdef
        try:
            fields = self._formdef.workflow.variables_formdef.fields
        except AttributeError:
            fields = []
        data = get_publisher().workflow_options_forced_value or self._formdef.workflow_options or {}
        for field in fields:
            # change field IDs as options are stored in data with their
            # varnames, not id.
            field.id = field.varname or field.id
            if hasattr(field, 'default_value') and data.get(field.varname) is None:
                if isinstance(field.default_value, str):
                    data[field.varname] = field.convert_value_from_str(field.default_value)
                else:
                    data[field.varname] = field.default_value
        super().__init__(fields, data)

    def inspect_keys(self):
        # don't display "parameter replacement" options
        return [x for x in self.varnames if '*' not in x]


class CardsSource:
    @classmethod
    def get_substitution_variables(cls):
        return {'cards': cls()}

    def inspect_keys(self):
        return []

    def __getattr__(self, attr):
        from wcs.carddef import CardDef, CardDefDoesNotExist

        if attr == 'inspect_collapse':
            return False
        try:
            return LazyFormDef(CardDef.get_by_urlname(attr, use_cache=True))
        except KeyError:
            raise CardDefDoesNotExist(attr)


class FormsSource:
    @classmethod
    def get_substitution_variables(cls):
        return {'forms': cls()}

    def inspect_keys(self):
        return []

    def __getattr__(self, attr):
        from wcs.formdef import FormDef
        from wcs.formdef_base import FormDefDoesNotExist

        if attr == 'inspect_collapse':
            return False
        try:
            return LazyFormDef(FormDef.get_by_urlname(attr, use_cache=True))
        except KeyError:
            raise FormDefDoesNotExist(attr)
