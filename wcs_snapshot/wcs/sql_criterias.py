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

import datetime
import json
import re
import time

import unidecode
from django.utils.timezone import is_aware, make_aware
from quixote import get_publisher

import wcs.qommon.storage
from wcs.qommon import misc


def like_escape(value):
    value = str(value or '').replace('\\', '\\\\')
    value = value.replace('_', '\\_')
    value = value.replace('%', '\\%')
    return value


def get_field_id(field):
    return 'f' + str(field.id).replace('-', '_').lower()


class Criteria(wcs.qommon.storage.Criteria):
    def __init__(self, attribute, value, **kwargs):
        self.attribute = attribute
        if '->' not in attribute:
            self.attribute = self.attribute.replace('-', '_')
        self.value = value
        self.field = kwargs.get('field')

    def format_value(self, value):
        if isinstance(value, time.struct_time):
            value = datetime.datetime(*value[:6])
        if isinstance(value, datetime.datetime) and not is_aware(value):
            value = make_aware(value, is_dst=True)
        return value

    def as_sql(self):
        value_is_list_of_int = (
            isinstance(self.value, list)
            and self.value
            and isinstance(self.value[0], int)  # all elements are of the same type
        )
        value_is_int = isinstance(self.value, int) or value_is_list_of_int

        if self.field and getattr(self.field, 'block_field', None):
            # eq: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' = 'value')
            # lt: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' < 'value')
            # lte: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' <= 'value')
            # gt: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' > 'value')
            # gte: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' >= 'value')
            # in: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' IN 'value')
            # between: EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa)
            #            WHERE aa->>'FOOBAR' >= 'value_min' AND aa->>'FOOBAR' < 'value_max')
            # with a NOT EXISTS and the opposite operator:
            # ne: NOT EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' = 'value')
            # note: aa->>'FOOBAR' can be written with an integer or bool cast
            attribute = "aa->>'%s'" % self.field.id
            jsonpath_operator_match = {'<': '<', '>': '>', '<=': '<=', '>=': '>=', '=': '=='}
            can_use_jsonpath = (
                not isinstance(self, Between)
                and self.sql_op in jsonpath_operator_match
                and not isinstance(self.value, list)
                and self.field.key != 'bool'
                and self.value != ''
                and self.transform_attribute('x') == 'x'
            )
            if can_use_jsonpath:
                # special case that can be optimized
                jsonpath_operator = jsonpath_operator_match[getattr(self, 'sql_op_exists', self.sql_op)]
                if self.field.key in ['item', 'string'] and value_is_int:
                    jsonpath_query = (
                        '$.data[*]."%s"[*] ? (@ %s %s || (@ %s "%s" && @ like_regex "^\\\\d+$"))'
                        % (
                            self.field.id,
                            jsonpath_operator,
                            self.value,
                            jsonpath_operator,
                            self.value,
                        )
                    )
                else:
                    jsonpath_query = '$.data[*]."%s"[*] ? (@ %s %s)' % (
                        self.field.id,
                        jsonpath_operator,
                        json.dumps(self.value),
                    )
                self.jsonpath = jsonpath_query
                return '%s @? %%(c%s)s' % (get_field_id(self.field.block_field), id(self.jsonpath))

            if self.field.key in ['item', 'string'] and value_is_int:
                if (
                    not isinstance(self, Between)
                    and getattr(self, 'sql_op_exists', self.sql_op) == '='
                    and isinstance(self.value, int)
                ):
                    # turn value into a string to avoid expensive casting in postgresql
                    self.value = str(self.value)
                else:
                    # integer cast of db values
                    attribute = "(CASE WHEN %s~E'^\\\\d{1,9}$' THEN (%s)::int ELSE NULL END)" % (
                        attribute,
                        attribute,
                    )
            elif self.field.key == 'bool':
                # bool cast of db values
                attribute = '(%s)::bool' % attribute
            else:
                attribute = self.transform_attribute(attribute)
            if isinstance(self, Between):
                return (
                    "%s(SELECT 1 FROM jsonb_array_elements(%s->'data') AS datas(aa) WHERE %s >= %%(c%s)s AND %s < %%(c%s)s)"
                    % (
                        getattr(self, 'sql_exists', 'EXISTS'),
                        get_field_id(self.field.block_field),
                        attribute,
                        id(self.value[0]),
                        attribute,
                        id(self.value[1]),
                    )
                )
            return "%s(SELECT 1 FROM jsonb_array_elements(%s->'data') AS datas(aa) WHERE %s %s %%(c%s)s)" % (
                getattr(self, 'sql_exists', 'EXISTS'),
                get_field_id(self.field.block_field),
                attribute,
                getattr(self, 'sql_op_exists', self.sql_op),
                id(self.value),
            )

        attribute = self.attribute

        if self.field and self.field.key == 'items':
            # eq: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa = 'value')
            # lt: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa < 'value')
            # lte: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa <= 'value')
            # gt: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa > 'value')
            # gte: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa >= 'value')
            # in: EXISTS (SELECT 1 FROM UNNEST(ITEMS) b(b(aa) WHERE aa IN 'value')
            # between: EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa >= 'value_min' AND aa < 'value_max')
            # with a NOT EXISTS and the opposite operator:
            # ne: NOT EXISTS (SELECT 1 FROM UNNEST(ITEMS) bb(aa) WHERE aa = 'value')
            # note: ITEMS is written with an integer cast or with a COALESCE expression
            if value_is_int:
                # integer cast of db values
                attribute = (
                    "CASE WHEN array_to_string(%s, '')~E'^\\\\d+$' THEN %s::int[] ELSE ARRAY[]::int[] END"
                    % (attribute, attribute)
                )
            else:
                # for none values
                attribute = 'COALESCE(%s, ARRAY[]::text[])' % attribute
            if isinstance(self, Between):
                return '%s(SELECT 1 FROM UNNEST(%s) bb(aa) WHERE %s >= %%(c%s)s AND %s < %%(c%s)s)' % (
                    getattr(self, 'sql_exists', 'EXISTS'),
                    attribute,
                    self.transform_attribute('aa'),
                    id(self.value[0]),
                    self.transform_attribute('aa'),
                    id(self.value[1]),
                )
            return '%s(SELECT 1 FROM UNNEST(%s) bb(aa) WHERE %s %s %%(c%s)s)' % (
                getattr(self, 'sql_exists', 'EXISTS'),
                attribute,
                self.transform_attribute('aa'),
                getattr(self, 'sql_op_exists', self.sql_op),
                id(self.value),
            )

        if self.field:
            if self.field.key == 'computed':
                attribute = "%s->>'data'" % self.attribute
            elif self.field.key in ['item', 'string'] and value_is_int:
                # integer cast of db values
                attribute = "(CASE WHEN %s~E'^\\\\d{1,9}$' THEN %s::int ELSE NULL END)" % (
                    attribute,
                    attribute,
                )
        attribute = self.transform_attribute(attribute)
        if isinstance(self, Between):
            return '%s %s %%(c%s)s AND %s %s %%(c%s)s' % (
                attribute,
                GreaterOrEqual.sql_op,
                id(self.value[0]),
                attribute,
                Less.sql_op,
                id(self.value[1]),
            )
        return '%s %s %%(c%s)s' % (attribute, self.sql_op, id(self.value))

    def transform_attribute(self, value):
        return value

    def as_sql_param(self):
        if hasattr(self, 'jsonpath'):
            return {'c%s' % id(self.jsonpath): self.jsonpath}
        return {'c%s' % id(self.value): self.format_value(self.value)}

    def get_referenced_varnames(self, formdef):
        from wcs.fields import Field

        value = getattr(self, 'value', None)
        if isinstance(value, (tuple, list, set)):
            for sub_value in value:
                yield from Field.get_referenced_varnames(formdef, sub_value)
        elif isinstance(value, str):
            yield from Field.get_referenced_varnames(formdef, value)


class Less(Criteria):
    sql_op = '<'


class Greater(Criteria):
    sql_op = '>'


class Equal(Criteria):
    sql_op = '='

    def as_sql(self):
        if self.value in ([], ()):
            return 'ARRAY_LENGTH(%s, 1) IS NULL' % self.attribute
        return super().as_sql()


class LessOrEqual(Criteria):
    sql_op = '<='


class GreaterOrEqual(Criteria):
    sql_op = '>='


class Between(Criteria):
    # min value is included, max value is excluded

    def as_sql_param(self):
        return {
            'c%s' % id(self.value[0]): self.format_value(self.value[0]),
            'c%s' % id(self.value[1]): self.format_value(self.value[1]),
        }


class NotEqual(Criteria):
    sql_op = '!='
    # in case of block field, we want to write this clause:
    # NOT EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' = 'value')
    # and not:
    # EXISTS (SELECT 1 FROM jsonb_array_elements(BLOCK->'data') AS datas(aa) WHERE aa->>'FOOBAR' != 'value')
    sql_exists = 'NOT EXISTS'
    sql_op_exists = '='

    def as_sql(self):
        if self.field and getattr(self.field, 'block_field', None):
            return super().as_sql()
        return '(%s is NULL OR %s)' % (self.attribute, super().as_sql())


class IEqual(Equal):
    def __init__(self, attribute, value, **kwargs):
        super().__init__(attribute, value, **kwargs)
        self.value = str(value).lower()

    def transform_attribute(self, value):
        return 'LOWER(%s)' % value


class StrictNotEqual(Criteria):
    sql_op = '!='


class Contains(Criteria):
    sql_op = 'IN'

    def as_sql(self):
        if not self.value:
            return 'FALSE'
        return super().as_sql()

    def as_sql_param(self):
        return {'c%s' % id(self.value): tuple(self.format_value(v) for v in self.value)}


class NotContains(Contains):
    sql_op = 'NOT IN'

    def as_sql(self):
        if not self.value:
            return 'TRUE'
        return super().as_sql()


class ArrayContains(Contains):
    sql_op = '@>'

    def as_sql_param(self):
        return {'c%s' % id(self.value): self.value}


class NotNull(Criteria):
    sql_op = 'IS NOT NULL'

    def __init__(self, attribute, **kwargs):
        super().__init__(attribute, value=None, **kwargs)

    def as_sql(self):
        return '%s %s' % (self.attribute, self.sql_op)

    def as_sql_param(self):
        return {}


class Null(Criteria):
    sql_op = 'IS NULL'

    def __init__(self, attribute, **kwargs):
        super().__init__(attribute, value=None, **kwargs)

    def as_sql(self):
        return '%s %s' % (self.attribute, self.sql_op)

    def as_sql_param(self):
        return {}


class Or(Criteria):
    def __init__(self, criterias, **kwargs):
        self.criterias = []
        for element in criterias:
            if isinstance(element, Criteria):
                sql_element = element
            else:
                sql_class = globals().get(element.__class__.__name__)
                sql_element = sql_class(**element.__dict__)
            self.criterias.append(sql_element)

    def as_sql(self):
        if not self.criterias:
            return '( FALSE )'
        return '( %s )' % ' OR '.join([x.as_sql() for x in self.criterias])

    def as_sql_param(self):
        d = {}
        for criteria in self.criterias:
            d.update(criteria.as_sql_param())
        return d

    def __repr__(self):
        return '<%s (%r)>' % (self.__class__.__name__, self.criterias)


class And(Or):
    def as_sql(self):
        return '( %s )' % ' AND '.join([x.as_sql() for x in self.criterias])


class Not(Criteria):
    def __init__(self, criteria, **kwargs):
        sql_class = globals().get(criteria.__class__.__name__)
        sql_element = sql_class(**criteria.__dict__)
        self.criteria = sql_element

    def as_sql(self):
        return 'NOT ( %s )' % self.criteria.as_sql()

    def as_sql_param(self):
        return self.criteria.as_sql_param()

    def get_referenced_varnames(self, formdef):
        yield from self.criteria.get_referenced_varnames(formdef)


class Intersects(Criteria):
    def as_sql(self):
        if not self.value:
            return 'ARRAY_LENGTH(%s, 1) IS NULL' % self.attribute
        return '%s && %%(c%s)s' % (self.attribute, id(self.value))

    def as_sql_param(self):
        return {'c%s' % id(self.value): list(self.value)}


class ILike(Criteria):
    sql_op = 'ILIKE'

    def __init__(self, attribute, value, **kwargs):
        super().__init__(attribute, value, **kwargs)
        self.value = '%' + like_escape(self.value) + '%'


phone_re = re.compile(
    r'''.*?(?P<phone>         # a phone number
        ((\+[1-9])|(\b0))     # starting with an international prefix, or 0
        [-\(\)\d\.\s/]{6,20}  # then a bunch of numbers/symbols
        \b)                   # till the end of the "word"''',
    re.X,
)


def normalize_phone_number_for_fts_if_needed(value):
    phone_match = phone_re.match(value)
    if phone_match and not re.match(r'^\d+-\d+$', phone_match.group('phone').strip()):
        # if it looks like a phone number, normalize it to its
        # "international/E164" format to match what's stored in the
        # database.
        phone_value = misc.normalize_phone_number_for_fts(phone_match.group('phone').strip())
        value = value.replace(phone_match.group('phone').strip(), phone_value)
    return value


class FtsMatch(Criteria):
    def __init__(self, value, extra_normalize=True, **kwargs):
        # make Criteria.__repr__ works
        self.attribute = 'fts'
        self.value = self.get_fts_value(value)
        if extra_normalize:
            self.value = normalize_phone_number_for_fts_if_needed(self.value)

    @classmethod
    def get_fts_value(cls, value):
        return unidecode.unidecode(value)

    def as_sql(self):
        return 'fts @@ plainto_tsquery(%%(c%s)s)' % id(self.value)

    def rank_sql(self):
        return 'ts_rank(fts, plainto_tsquery(%%(c%s)s))' % id(self.value)


class ExtendedFtsMatch(Criteria):
    def __init__(self, value, context=None, extra_normalize=True, **kwargs):
        """
        value: the query from the end-user
        context: used to filter the search tokens used in wcs_tsquery. Possible contexts
            are "carddata_XYZ", "formdata_XYZ" and "formdefs" (this function accepts
            CardDef and FormDef instances instead of string for simplicity).
            If no context is given, the search is extended using all tokens instead of a
            subset
        extra_normalize: attempt to normalize the phone number in the query
        """

        # make Criteria.__repr__ works
        self.attribute = 'fts'

        # build context
        self.context = context
        if self.context is not None:
            from wcs.carddef import CardDef
            from wcs.formdef import FormDef

            # convert to text context based on supported contexts.
            if isinstance(self.context, CardDef):
                self.context = f'carddata_{self.context.id}'
            elif isinstance(self.context, FormDef):
                self.context = f'formdata_{self.context.id}'
            elif not isinstance(self.context, str):
                raise Exception('Invalid ExtendedFtsMatch context')

        # build value
        self.value = self.get_fts_value(value)
        if extra_normalize:
            self.value = normalize_phone_number_for_fts_if_needed(self.value)

    @classmethod
    def get_fts_value(cls, value):
        return unidecode.unidecode(value)

    def _tsquery_sql(self):
        if get_publisher().has_site_option('enable-new-fts'):
            if self.context is None:
                return 'wcs_tsquery(%%(c%s)s)' % id(self.value)
            return 'wcs_tsquery(%%(c%s)s, %%(c%s)s)' % (id(self.value), id(self.context))
        return 'plainto_tsquery(%%(c%s)s)' % id(self.value)

    def as_sql(self):
        return 'fts @@ %s' % self._tsquery_sql()

    def as_sql_param(self):
        if self.context is not None and get_publisher().has_site_option('enable-new-fts'):
            return {
                'c%s' % id(self.value): self.format_value(self.value),
                'c%s' % id(self.context): self.context,
            }

        return {
            'c%s' % id(self.value): self.format_value(self.value),
        }

    def rank_sql(self):
        return 'ts_rank(fts, %s)' % self._tsquery_sql()


class ElementEqual(Criteria):
    def __init__(self, attribute, key, value, **kwargs):
        super().__init__(attribute, value)
        self.key = key

    def as_sql(self):
        return "%s->>'%s' = %%(c%s)s" % (self.attribute, self.key, id(self.value))


class ElementILike(ElementEqual):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value = '%' + like_escape(self.value) + '%'

    def as_sql(self):
        return "%s->>'%s' ILIKE %%(c%s)s" % (self.attribute, self.key, id(self.value))


class ElementIntersects(ElementEqual):
    def as_sql(self):
        if not self.value:
            return 'FALSE'
        if not isinstance(self.value, (tuple, list, set)):
            self.value = [self.value]
        else:
            self.value = list(self.value)
        return "EXISTS(SELECT 1 FROM jsonb_array_elements_text(%s->'%s') foo WHERE foo = ANY(%%(c%s)s))" % (
            self.attribute,
            self.key,
            id(self.value),
        )


class Nothing(Criteria):
    def __init__(self, *args, **kwargs):
        self.attribute = None
        self.value = None

    def as_sql(self):
        return 'FALSE'

    def as_sql_param(self):
        return {}


class Distance(Criteria):
    def __init__(self, point, distance, **kwargs):
        self.point = point  # {'lat': ..., 'lon': ...}
        self.distance = distance  # in meters

    def as_sql(self):
        # simplest distance approximation <https://www.mkompf.com/gps/distcalc.html>
        return f'''(111300 * SQRT(POWER((auto_geoloc[0] - {self.point['lon']}) *
                                         COS((auto_geoloc[1] + {self.point['lat']}) / 2 * 0.01745), 2)
                                  + POWER(auto_geoloc[1] - {self.point['lat']}, 2)))
                    < {self.distance}'''

    def as_sql_param(self):
        return {}


class StatusReachedTimeoutCriteria(Criteria):
    """
    Criteria to check a timeout against a status change in the evolution table.
    For this criteria to match, there must be at least one row older than the given duration (in days).
    """

    def __init__(self, formdef, statuses, duration, **kwargs):
        # Note : no attribute, we will use an EXISTS, status as value, duration will go straight in...
        super().__init__('', statuses, **kwargs)
        self.formdef = formdef
        self.duration = duration

    def as_sql(self):
        duration = int(self.duration)
        statuses = 'c%s' % id(self.value)
        formdef_table = self.formdef._table_name
        formdef_evolution_table = self.formdef._table_name + '_evolutions'
        return f'''EXISTS(
            SELECT 1 FROM {formdef_evolution_table}
            WHERE {formdef_evolution_table}.formdata_id = {formdef_table}.id 
                AND {formdef_evolution_table}.status = ANY(%({statuses})s)
                AND {formdef_evolution_table}.time <= NOW() - {duration} * interval '1 day')'''


class ArrayPrefixMatch(Criteria):
    def __init__(self, attribute, value, **kwargs):
        value = value + '%'
        super().__init__(attribute, value, **kwargs)

    def as_sql(self):
        return '''exists (select 1 from unnest(%s) v where v LIKE %%(c%s)s)''' % (
            self.attribute,
            id(self.value),
        )


class BlockAbsent(Criteria):
    def __init__(self, attribute, **kwargs):
        super().__init__(attribute, value=None, **kwargs)

    def as_sql(self):
        block_field_id = get_field_id(self.field.block_field)
        if self.field.key == 'bool':
            non_empty_expr = f'''(@."{self.field.id}" == false || @."{self.field.id}" == true)'''
        elif self.field.key in ('numeric', 'file', 'map'):
            non_empty_expr = f'''(@."{self.field.id}" != null)'''
        else:
            non_empty_expr = f'''(@."{self.field.id}" != null && @."{self.field.id}" != "")'''
        return (
            f'''{block_field_id} IS NULL OR '''
            f'''NOT EXISTS(SELECT 1 FROM jsonb_path_query({block_field_id}, '$.data[*] ?  {non_empty_expr}'))'''
        )

    def as_sql_param(self):
        return {}
