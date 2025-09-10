# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import urllib.parse

from quixote import get_publisher, get_request, get_session, redirect
from quixote.html import TemplateIO, htmltext

from wcs.backoffice.filter_fields import FilterField
from wcs.backoffice.pagination import pagination_links
from wcs.roles import logged_users_role
from wcs.sql_criterias import (
    Contains,
    ExtendedFtsMatch,
    Intersects,
    Not,
    NotContains,
    Nothing,
    Null,
    StrictNotEqual,
)

from ..qommon import _, misc


class FormDefUI:
    def __init__(self, formdef):
        self.formdef = formdef

    def listing(
        self,
        fields,
        *,
        selected_filter='all',
        selected_filter_operator='eq',
        url_action=None,
        items=None,
        offset=0,
        limit=0,
        query=None,
        order_by=None,
        criterias=None,
        include_checkboxes=False,
    ):
        # noqa pylint: disable=too-many-arguments

        if not items:
            if offset and not limit:
                limit = int(get_publisher().get_site_option('default-page-size') or 20)
            if not criterias:
                criterias = []
            criterias.append(Null('anonymised'))
            items, total_count = self.get_listing_items(
                fields,
                selected_filter,
                selected_filter_operator,
                offset,
                limit,
                query,
                order_by,
                criterias=criterias,
            )

            if offset > total_count:
                get_request().form['offset'] = '0'
                return redirect('?' + urllib.parse.urlencode(get_request().form))

        r = TemplateIO(html=True)

        if self.formdef.workflow:
            colours = []
            for status in self.formdef.workflow.possible_status:
                if status.colour and status.colour != '#FFFFFF':
                    fg_colour = misc.get_foreground_colour(status.colour)
                    colours.append((status.id, status.colour, fg_colour))
            if colours:
                r += htmltext('<style>')
                for status_id, bg_colour, fg_colour in colours:
                    r += htmltext(
                        'tr.status-%s-wf-%s td.cell-status { '
                        'background-color: %s !important; color: %s !important; }\n'
                        % (self.formdef.workflow.id, status_id, bg_colour, fg_colour)
                    )
                r += htmltext('</style>')

        r += htmltext('<div id="listing-container">')
        r += htmltext('<table id="listing" class="main compact">')

        r += htmltext('<colgroup>')
        if include_checkboxes:
            r += htmltext('<col/>')  # checkbox
        r += htmltext('<col/>')  # lock
        r += htmltext('<col/>')
        r += htmltext('<col/>')
        for f in fields:
            r += htmltext('<col />')
        r += htmltext('</colgroup>')

        r += htmltext('<thead><tr>')

        # group adjacent same-block columns
        previous_block_subfield = None
        grouped_columns = []
        for f in fields:
            if getattr(f, 'block_field', None):
                if previous_block_subfield and f.block_field.id == previous_block_subfield.block_field.id:
                    previous_block_subfield.include_block_label = False
                    grouped_columns[-1]['count'] += 1
                    f.include_block_label = False
                    f.block_column = grouped_columns[-1]
                else:
                    grouped_columns.append({'label': f.block_field.label, 'count': 1})
                    f.include_block_label = True
                    f.block_column = grouped_columns[-1]
                previous_block_subfield = f
            else:
                previous_block_subfield = None
                grouped_columns.append({'count': 1})

        thead_height = 1
        if any(x.get('count') > 1 for x in grouped_columns):
            # if there are adjacent same-block columns, the thead will have two rows
            thead_height = 2

        if self.formdef.workflow.criticality_levels:
            r += htmltext(
                f'<th rowspan="{thead_height}" class="criticality-level-cell" '
                'data-field-sort-key="criticality_level"><span></span></th>'
            )
        else:
            r += htmltext(f'<th rowspan="{thead_height}"></th>')  # lock
        if include_checkboxes:
            r += htmltext(
                f'<th rowspan="{thead_height}" class="select"><input type="checkbox" id="top-select"/>'
            )
            r += htmltext(
                ' <span id="info-all-rows"><label><input type="checkbox" name="select[]" value="_all"/> %s</label></span></th>'
            ) % _('Run selected action on all pages')

        def get_column_title(label):
            if len(label) < 20:
                return htmltext('<span>%s</span>') % label
            return htmltext('<span title="%s">%s</span>') % (label, misc.ellipsize(label, 20))

        for f in fields:
            if isinstance(f, FilterField):
                field_sort_key = f.id
                if f.id == 'time':
                    field_sort_key = 'receipt_time'
                elif f.id in ('user-label', 'submission-agent'):
                    field_sort_key = None
            elif getattr(f, 'is_related_field', False):
                field_sort_key = None
            elif getattr(f, 'block_field', None) and f.block_field.get_max_items() != 1:
                # allow sorting on a field of block field if there is one item only
                field_sort_key = None
            else:
                field_sort_key = 'f%s' % f.contextual_id

            th_rowspan = f' rowspan="{thead_height}"' if thead_height > 1 else ''
            if getattr(f, 'block_field', None):
                if f.include_block_label:
                    # isolated block subfield column
                    f.label = '%s / %s' % (f.block_field.label, f.label)
                else:
                    # grouped subfields first row, block name
                    if not f.block_column.get('seen'):
                        f.block_column['seen'] = True
                        r += htmltext(f'<th class="col-group" colspan="{f.block_column["count"]}">')
                        r += get_column_title(f.block_field.label)
                        r += htmltext('</th>')
                    f.field_sort_key = field_sort_key
                    continue

            if field_sort_key:
                r += htmltext(f'<th{th_rowspan} data-field-sort-key="{field_sort_key}">')
            else:
                r += htmltext(f'<th{th_rowspan}>')
            r += get_column_title(f.label)
            r += htmltext('</th>')

        if thead_height > 1:
            # add individual columns for grouped subfields
            r += htmltext('</tr><tr>')
            for f in fields:
                if getattr(f, 'block_field', None) and not f.include_block_label:
                    if f.field_sort_key:
                        r += htmltext(f'<th class="col-subfield" data-field-sort-key="{f.field_sort_key}">')
                    else:
                        r += htmltext('<th class="col-subfield">')
                    r += get_column_title(f.label)
                    r += htmltext('</th>')

        r += htmltext('</tr></thead>')
        r += htmltext('<tbody>')
        r += htmltext(self.tbody(fields, items, url_action, include_checkboxes=include_checkboxes))
        r += htmltext('</tbody>')
        r += htmltext('</table>')
        r += htmltext('</div>')  # <!-- #listing-container -->

        # add links to paginate
        r += pagination_links(offset, limit, total_count)

        return r.getvalue()

    def get_status_criterias(self, selected_filter, selected_filter_operator, user, raise_error=False):
        formdata_class = self.formdef.data_class()
        criterias = []
        selected_filters = [selected_filter]
        if selected_filter_operator in ['in', 'not_in']:
            selected_filters = selected_filter.split('|')
        if any(value for value in selected_filters if value == 'all'):
            if selected_filter_operator in ['ne', 'not_in']:
                # nothing
                return [Nothing()]
            # other operators: no filtering on status, as we want to get 'all'
        elif any(value for value in selected_filters if value == 'waiting'):
            if selected_filter_operator in ['in', 'not_in']:
                # it's difficult to mix waiting with other statuses: return nothing
                return [Nothing()]
            user_roles = [logged_users_role().id] + user.get_roles()
            actionable_criteria = formdata_class.get_actionable_ids_criteria(user_roles)
            if selected_filter_operator == 'ne':
                criterias.append(Not(actionable_criteria))
            else:
                criterias.append(actionable_criteria)
        else:
            # build selected status list
            applied_filters = []
            for value in selected_filters:
                if value == 'pending':
                    applied_filters += [
                        'wf-%s' % x.id for x in self.formdef.workflow.get_not_endpoint_status()
                    ]
                elif value == 'done':
                    applied_filters += ['wf-%s' % x.id for x in self.formdef.workflow.get_endpoint_status()]
                else:
                    applied_filters += ['wf-%s' % value]

            if selected_filter_operator in ['ne', 'not_in']:
                # exclude selected status list
                criterias.append(NotContains('status', set(applied_filters)))
            else:
                # only selected status list
                criterias.append(Contains('status', set(applied_filters)))
        return criterias

    def get_listing_item_criterias(
        self,
        selected_filter='all',
        selected_filter_operator='eq',
        query=None,
        user=None,
        criterias=None,
        anonymise=False,
    ):
        criterias = [] or criterias[:]
        criterias.append(StrictNotEqual('status', 'draft'))
        criterias += self.get_status_criterias(selected_filter, selected_filter_operator, user)
        if query:
            criterias.append(ExtendedFtsMatch(query, self.formdef))
        if not anonymise:
            # as we are in the backoffice, we don't have to care about the
            # situation where the user is the submitter, and we limit ourselves
            # to consider treating roles.
            if not user.is_admin:
                user_roles = [str(x) for x in user.get_roles()]
                criterias.append(Intersects('concerned_roles_array', user_roles))
        return criterias

    def get_listing_item_ids(
        self,
        selected_filter='all',
        selected_filter_operator='eq',
        query=None,
        order_by=None,
        user=None,
        criterias=None,
        anonymise=False,
        offset=None,
        limit=None,
    ):
        formdata_class = self.formdef.data_class()

        if order_by and not anonymise:
            order_by = self.formdef.get_order_by(order_by)
        elif not anonymise and query:
            order_by = 'rank'
        else:
            order_by = '-id'

        if order_by in ('id', '-id') and self.formdef.id_template:
            order_by = order_by[:-2] + 'id_display'

        criterias = self.get_listing_item_criterias(
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            query=query,
            user=user,
            criterias=criterias,
            anonymise=anonymise,
        )

        item_ids = list(formdata_class.get_sorted_ids(order_by, clause=criterias, offset=offset, limit=limit))
        return item_ids

    def get_listing_items_total_count(
        self,
        selected_filter='all',
        selected_filter_operator='eq',
        query=None,
        order_by=None,
        user=None,
        criterias=None,
        anonymise=False,
    ):
        formdata_class = self.formdef.data_class()

        criterias = self.get_listing_item_criterias(
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            query=query,
            user=user,
            criterias=criterias,
            anonymise=anonymise,
        )

        return formdata_class.count(clause=criterias)

    def get_listing_items(
        self,
        fields=None,
        selected_filter='all',
        selected_filter_operator='eq',
        offset=None,
        limit=None,
        query=None,
        order_by=None,
        *,
        user=None,
        criterias=None,
        anonymise=False,
        itersize=200,
    ):  # noqa pylint: disable=too-many-arguments
        assert itersize >= 1, 'itersize must be positive'
        user = user or get_request().user
        formdata_class = self.formdef.data_class()

        if not offset:
            offset = 0

        item_ids = self.get_listing_item_ids(
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            query=query,
            order_by=order_by,
            user=user,
            criterias=criterias,
            anonymise=anonymise,
            offset=offset,
            limit=limit,
        )

        total_count = self.get_listing_items_total_count(
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            query=query,
            user=user,
            criterias=criterias,
            anonymise=anonymise,
        )

        items = formdata_class.get_ids_iterator(
            item_ids,
            keep_order=True,
            itersize=itersize,
            fields=fields,
        )

        return (items, total_count)

    def tbody(self, fields=None, items=None, url_action=None, include_checkboxes=False):
        r = TemplateIO(html=True)
        if url_action:
            pass
            # url_action = '/' + url_action
        else:
            url_action = ''
        user = get_request().user
        user_roles = set(user.get_roles())
        visited_objects = get_session().get_visited_objects(exclude_user=user.id)
        include_criticality_level = bool(self.formdef.workflow.criticality_levels)
        for i, filled in enumerate(items):
            classes = ['status-%s-%s' % (filled.formdef.workflow.id, filled.status)]
            if i % 2:
                classes.append('even')
            else:
                classes.append('odd')

            if filled.get_object_key() in visited_objects:
                classes.append('advisory-lock')
            if filled.backoffice_submission:
                classes.append('backoffice-submission')

            style = ''
            if include_criticality_level:
                try:
                    level = filled.get_criticality_level_object()
                except IndexError:
                    style = ''
                else:
                    classes.append('criticality-level')
                    style = ' style="border-left-color: %s;"' % level.colour

            link = str(filled.identifier) + '/'
            data = ' data-link="%s"' % link
            if filled.anonymised:
                data += ' data-anonymised="true"'
            r += htmltext('<tr class="%s"%s>' % (' '.join(classes), data))
            if include_criticality_level:
                r += htmltext('<td class="criticality-level-cell" %s></td>' % style)  # criticality_level
            else:
                r += htmltext('<td class="lock-cell"></td>')  # lock
            if include_checkboxes:
                r += htmltext('<td class="select"><input type="checkbox" name="select[]" ')
                r += htmltext('value="%s"') % filled.id
                workflow_roles = {}
                if self.formdef.workflow_roles:
                    workflow_roles.update(self.formdef.workflow_roles)
                if filled.workflow_roles:
                    workflow_roles.update(filled.workflow_roles)
                for function_key, function_value in workflow_roles.items():
                    if isinstance(function_value, (str, int)):
                        # single role, defined at formdef level
                        # (int are for compatibility with very old forms)
                        function_values = {str(function_value)}
                    else:
                        # list of roles (or none), defined at formdata level
                        function_values = set(function_value or [])
                    if user_roles.intersection(function_values):
                        # dashes are replaced by underscores to prevent HTML5
                        # normalization to CamelCase.
                        r += htmltext(' data-is_%s="true" ' % function_key.replace('-', '_'))
                    r += htmltext(' data-status_%s="true" ' % filled.status.removeprefix('wf-'))
                r += htmltext('/></td>')
            for i, f in enumerate(fields):
                field_value = filled.get_field_view_value(f, max_length=30)
                if f.key == 'id':
                    r += htmltext('<td class="cell-id"><a href="%s%s">%s</a></td>') % (
                        link,
                        url_action,
                        field_value,
                    )
                    continue
                css_class = {
                    'time': 'cell-time',
                    'last_update_time': 'cell-time',
                    'user-label': 'cell-user',
                    'status': 'cell-status',
                    'anonymised': 'cell-anonymised',
                    'submission-agent': 'cell-submission-agent',
                }.get(f.key)
                if css_class:
                    r += htmltext('<td class="%s">' % css_class)
                else:
                    r += htmltext('<td>')
                if hasattr(field_value, 'replace'):
                    field_value = field_value.replace('[download]', str('%sdownload' % link))
                else:
                    field_value = str(field_value)
                r += field_value
                r += htmltext('</td>')
            r += htmltext('</tr>\n')
        return r.getvalue()
