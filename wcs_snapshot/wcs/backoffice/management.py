# w.c.s. - web application for online forms
# Copyright (C) 2005-2015  Entr'ouvert
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
import csv
import datetime
import html
import io
import json
import re
import tempfile
import types
import urllib.parse
import zipfile

import vobject
from bleach import Cleaner
from bleach.css_sanitizer import CSSSanitizer
from django.utils.encoding import force_str
from django.utils.timezone import is_naive, make_aware, make_naive, now
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.errors import RequestError
from quixote.html import TemplateIO, htmlescape, htmltag, htmltext
from quixote.http_request import parse_query

from wcs.api_utils import get_query_flag, get_user_from_api_query_string
from wcs.backoffice import filter_fields
from wcs.backoffice.pagination import pagination_links
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.conditions import Condition
from wcs.formdata import FormData, NoContentSnapshotAt
from wcs.formdef import FormDef
from wcs.forms.backoffice import FormDefUI
from wcs.forms.common import FormStatusPage, TempfileDirectoryMixin
from wcs.roles import get_user_roles, logged_users_role
from wcs.sql import ApiAccess
from wcs.sql_criterias import (
    Contains,
    Distance,
    ElementIntersects,
    Equal,
    ExtendedFtsMatch,
    GreaterOrEqual,
    Intersects,
    LessOrEqual,
    Not,
    NotContains,
    NotEqual,
    Nothing,
    NotNull,
    Null,
    StrictNotEqual,
    get_field_id,
)
from wcs.tracking_code import TrackingCode
from wcs.variables import LazyFieldVar, LazyFormDefObjectsManager
from wcs.workflows import WorkflowStatus, WorkflowStatusItem, item_classes, template_on_formdata

from ..qommon import _, audit, errors, ezt, get_cfg, misc, ngettext, ods, pgettext_lazy, template
from ..qommon.afterjobs import AfterJob
from ..qommon.evalutils import make_datetime
from ..qommon.form import (
    CheckboxWidget,
    DateWidget,
    Form,
    HiddenWidget,
    HtmlWidget,
    MapWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    TextWidget,
    WidgetList,
    WysiwygTextWidget,
)
from ..qommon.misc import ellipsize, get_type_name, is_ascii_digit, unlazy
from ..qommon.substitution import CompatibilityNamesDict, SubtreeVar
from ..qommon.template import DjangoTemplateSyntaxError, Template
from ..qommon.upload_storage import PicklableUpload
from .submission import FormFillPage


def get_common_statuses():
    return [
        ('waiting', _('Waiting for an action'), 'waiting'),
        ('open', pgettext_lazy('formdata', 'Open'), 'open'),
        ('done', _('Done'), 'done'),
        ('all', _('All'), 'all'),
    ]


def geojson_formdatas(formdatas, geoloc_key='base', fields=None):
    geojson = {'type': 'FeatureCollection', 'features': []}

    get_text_value = None
    for formdata in formdatas:
        if get_text_value is None:
            if isinstance(formdata.formdef, CardDef):
                get_text_value = lambda x: x.default_digest or x.get_display_name()
            else:
                get_text_value = lambda x: x.get_display_name()

        if not formdata.geolocations or geoloc_key not in formdata.geolocations:
            continue
        coords = misc.normalize_geolocation(formdata.geolocations[geoloc_key])
        if not coords:
            continue
        status = formdata.get_status()
        try:
            status_colour = status.colour
        except AttributeError:
            status_colour = '#ffffff'

        display_fields = []
        formdata_backoffice_url = formdata.get_url(backoffice=True)
        if fields:
            for field in fields:
                if field.key in ('map', 'computed', 'card-id-field'):
                    continue
                html_value = formdata.get_field_view_value(field, max_length=60)
                if hasattr(html_value, 'replace'):
                    html_value = html_value.replace('[download]', '%sdownload' % formdata_backoffice_url)
                value = formdata.get_field_view_value(field)
                if field.key == 'block':
                    # return display value for block fields, not the internal structure
                    value = formdata.data.get(f'{field.id}_display')
                if not html_value and not value:
                    continue

                geojson_infos = {
                    'varname': field.varname,
                    'label': field.label,
                    'value': str(value),
                    'html_value': str(htmlescape(html_value)),
                }
                if field.key == 'file' and not getattr(field, 'block_field', None):
                    raw_value = formdata.data.get(field.id)
                    if raw_value.has_redirect_url():
                        geojson_infos['file_url'] = field.get_download_url(file_value=raw_value)
                display_fields.append(geojson_infos)

        feature = {
            'type': 'Feature',
            'properties': {
                'id': str(formdata.get_display_id()),
                'raw_id': str(formdata.id),
                'text': get_text_value(formdata),
                'name': str(htmlescape(get_text_value(formdata))),
                'url': formdata_backoffice_url,
                'status_name': str(htmlescape(status.name)) if status else str(_('Unknown')),
                'status_colour': status_colour,
                'view_label': force_str(_('View')),
            },
            'geometry': {
                'type': 'Point',
                'coordinates': [coords['lon'], coords['lat']],
            },
        }
        if display_fields:
            feature['properties']['display_fields'] = display_fields
        geojson['features'].append(feature)

    return geojson


def get_field_criteria_label(field):
    if getattr(field, 'block_field', None):
        return '%s / %s' % (field.block_field.label, field.label)
    return field.label


def get_field_selection_label(field):
    label = htmltext('<span class="field--selection-label">')
    label += htmltext('<span class="field--selection-label--text">')
    label += misc.ellipsize(get_field_criteria_label(field), 70)
    label += htmltext('</span>')
    if getattr(field, 'is_backoffice_field', False):
        label += htmltext(' <span class="field--selection-label--suffix">(%s)</span>') % _('backoffice field')
    elif getattr(field, 'parent_page_field', None):
        label += htmltext(' <span class="field--selection-label--suffix">(%s)</span>') % misc.ellipsize(
            field.parent_page_field.label, 30, truncate='…'
        )
    label += htmltext('</span>')
    return label


class ManagementDirectory(Directory):
    _q_exports = ['', 'forms', 'listing', 'statistics', 'lookup', 'count', 'geojson', 'map']
    section = 'management'

    def add_breadcrumb(self):
        get_response().breadcrumb.append(('management/', _('Management')))

    def is_accessible(self, user, traversal=False):
        return user.can_go_in_backoffice()

    def _q_traverse(self, path):
        self.add_breadcrumb()
        get_response().set_backoffice_section(self.section)
        return super()._q_traverse(path)

    def _q_index(self):
        if get_publisher().has_site_option('default-to-global-view'):
            return redirect('listing')
        return redirect('forms')

    def forms(self):
        get_response().set_title(_('Management'))
        formdefs = FormDef.select(order_by='name', ignore_errors=True, lightweight=True)
        if len(formdefs) == 0:
            return self.empty_site_message(_('Forms'))
        get_response().filter['sidebar'] = self.get_sidebar()
        r = TemplateIO(html=True)
        r += get_session().display_message()

        user = get_request().user
        user_roles = [logged_users_role().id] + (user.get_roles() if user else [])

        forms_without_pending_stuff = []
        forms_with_pending_stuff = []

        from wcs import sql

        actionable_counts = sql.get_actionable_counts(user_roles)
        total_counts = sql.get_total_counts(user_roles)

        def append_form_entry(formdef):
            count_forms = total_counts.get(str(formdef.id)) or 0
            waiting_forms_count = actionable_counts.get(str(formdef.id)) or 0
            if waiting_forms_count == 0:
                forms_without_pending_stuff.append((formdef, waiting_forms_count, count_forms))
            else:
                forms_with_pending_stuff.append((formdef, waiting_forms_count, count_forms))

        if user:
            for formdef in formdefs:
                if user.is_admin or formdef.is_of_concern_for_user(user):
                    append_form_entry(formdef)

        def top_action_links(r):
            r += htmltext('<span class="actions">')
            r += htmltext('<a data-base-href="listing" href="listing">%s</a>') % _('Global View')
            for formdef in formdefs:
                if formdef.geolocations:
                    url = 'map'
                    if get_request().get_query():
                        url += '?' + get_request().get_query()
                    r += htmltext(' <a data-base-href="map" href="%s">' % url)
                    r += htmltext('%s</a>') % _('Map View')
                    break
            r += htmltext('</span>')

        if forms_with_pending_stuff:
            r += htmltext('<div id="appbar">')
            r += htmltext('<h2>%s</h2>') % _('Forms in your care')
            top_action_links(r)
            r += htmltext('</div>')
            r += self.display_forms(forms_with_pending_stuff)

        if forms_without_pending_stuff:
            r += htmltext('<div id="appbar">')
            r += htmltext('<h2>%s</h2>') % _('Other Forms')
            if not forms_without_pending_stuff:
                top_action_links(r)
            r += htmltext('</div>')
            r += self.display_forms(forms_without_pending_stuff)

        if not (forms_with_pending_stuff or forms_without_pending_stuff):
            r += htmltext('<div id="appbar">')
            r += htmltext('<h2>%s</h2>') % _('Forms')
            top_action_links(r)
            r += htmltext('</div>')

        return r.getvalue()

    def get_sidebar(self):
        r = TemplateIO(html=True)
        r += self.get_lookup_sidebox('forms')
        if not get_publisher().has_site_option('disable-internal-statistics'):
            r += htmltext('<div class="bo-block">')
            r += htmltext('<ul id="sidebar-actions">')
            r += htmltext('<li class="stats"><a href="statistics">%s</a></li>') % _('Global statistics')
            r += htmltext('</ul>')
            r += htmltext('</div>')
        return r.getvalue()

    def lookup(self):
        query = get_request().form.get('query', '').strip()
        from wcs import sql

        formdata = None
        get_session().add_message(_('No such tracking code or identifier.'))

        formdatas = sql.AnyFormData.select([Equal('id_display', query)])
        if formdatas:
            formdata = formdatas[0]
            if formdata.is_draft():
                get_session().add_message(
                    _('This identifier matches a draft form, it is not yet available for management.'),
                )
                formdata = None
        elif any(x for x in FormDef.select(lightweight=True) if x.enable_tracking_codes):
            try:
                tracking_code = TrackingCode.get(query)
                formdata = tracking_code.formdata
                if formdata.is_draft():
                    get_session().add_message(
                        _('This tracking code matches a draft form, it is not yet available for management.'),
                    )
                    formdata = None
                else:
                    get_session().mark_anonymous_formdata(formdata)
            except KeyError:
                pass

        if formdata:
            get_session().message = None
            return redirect(formdata.get_url(backoffice=True))

        back_place = get_request().form.get('back')
        if back_place not in ('listing', 'forms'):
            back_place = '.'  # auto
        return redirect(back_place)

    def get_lookup_sidebox(self, back_place=''):
        r = TemplateIO(html=True)
        r += htmltext('<div id="lookup-box">')
        r += htmltext('<h3>%s</h3>' % _('Look up by tracking code or identifier'))
        r += htmltext('<form action="lookup">')
        r += htmltext('<input type="hidden" name="back" value="%s"/>') % back_place
        r += htmltext('<input class="inline-input" size="12" name="query"/>')
        r += htmltext('<button>%s</button>') % _('Look up')
        r += htmltext('</form>')
        r += htmltext('</div>')
        return r.getvalue()

    def get_global_listing_sidebar(self, limit=None, offset=None, order_by=None, view=''):
        get_response().add_javascript(['jquery.js'])
        form = Form(
            use_tokens=False, id='listing-settings', method='get', action=view, **{'class': 'global-filters'}
        )
        params = get_request().form
        form.add(
            SingleSelectWidget,
            'status',
            title=_('Status'),
            options=get_common_statuses(),
            value=params.get('status'),
        )
        form.add(DateWidget, 'start', title=_('Start Date'), value=params.get('start'))
        form.add(DateWidget, 'end', title=_('End Date'), value=params.get('end'))

        categories = Category.select()
        if categories:
            Category.sort_by_position(categories)
            category_options = [(None, pgettext_lazy('categories', 'All'), '')] + [
                (str(x.id), x.name, str(x.id)) for x in categories
            ]
            category_slugs = (params.get('category_slugs') or '').split(',')
            category_slugs = [c.strip() for c in category_slugs if c.strip()]
            for i, category in enumerate([c for c in categories if c.url_name in category_slugs]):
                params['category_ids$element%s' % i] = str(category.id)
            form.add(
                WidgetList,
                'category_ids',
                title=_('Categories'),
                element_type=SingleSelectWidget,
                add_element_label=_('Add Category'),
                element_kwargs={
                    'render_br': False,
                    'options': category_options,
                },
            )

        if get_cfg('submission-channels', {}).get('include-in-global-listing'):
            form.add(
                SingleSelectWidget,
                'submission_channel',
                title=_('Channel'),
                options=[(None, pgettext_lazy('channel', 'All'), '')]
                + [(x, y, x) for x, y in FormData.get_submission_channels().items()],
                value=params.get('submission_channel'),
            )

        form.add(StringWidget, 'q', title=_('Full text search'), value=params.get('q'))

        if not offset:
            offset = 0
        if not limit:
            limit = int(get_publisher().get_site_option('default-page-size') or 20)
        if not order_by:
            order_by = get_publisher().get_site_option('default-sort-order') or '-receipt_time'
        form.add_hidden('offset', offset)
        form.add_hidden('limit', limit)
        form.add_hidden('order_by', order_by)

        form.add_submit('submit', _('Submit'))

        r = TemplateIO(html=True)
        r += self.get_lookup_sidebox('listing')
        r += htmltext('<div>')
        r += htmltext('<h3>%s</h3>') % _('Filters')
        r += form.render()
        r += htmltext('</div>')

        return r.getvalue()

    def get_stats_sidebar(self):
        get_response().add_javascript(['jquery.js'])
        form = Form(use_tokens=False)
        form.add(DateWidget, 'start', title=_('Start Date'))
        form.add(DateWidget, 'end', title=_('End Date'))
        form.add_submit('submit', _('Submit'))

        r = TemplateIO(html=True)
        r += htmltext('<h3>%s</h3>') % _('Period')
        r += form.render()

        r += htmltext('<h3>%s</h3>') % _('Shortcuts')
        r += htmltext('<ul>')  # presets
        current_month_start = datetime.datetime.now().replace(day=1)
        start = current_month_start.strftime(misc.date_format())
        r += htmltext(' <li><a href="?start=%s">%s</a>') % (start, _('Current Month'))
        previous_month_start = current_month_start - datetime.timedelta(days=2)
        previous_month_start = previous_month_start.replace(day=1)
        start = previous_month_start.strftime(misc.date_format())
        end = current_month_start.strftime(misc.date_format())
        r += htmltext(' <li><a href="?start=%s&end=%s">%s</a>') % (start, end, _('Previous Month'))

        current_year_start = datetime.datetime.now().replace(month=1, day=1)
        start = current_year_start.strftime(misc.date_format())
        r += htmltext(' <li><a href="?start=%s">%s</a>') % (start, _('Current Year'))
        previous_year_start = current_year_start.replace(year=current_year_start.year - 1)
        start = previous_year_start.strftime(misc.date_format())
        end = current_year_start.strftime(misc.date_format())
        r += htmltext(' <li><a href="?start=%s&end=%s">%s</a>') % (start, end, _('Previous Year'))

        return r.getvalue()

    def statistics(self):
        get_response().set_title(_('Global statistics'))
        get_response().breadcrumb.append(('statistics', _('Global statistics')))

        if not (FormDef.exists()):
            r = TemplateIO(html=True)
            r += htmltext('<div class="top-title">')
            r += htmltext('<h2>%s</h2>') % _('Global statistics')
            r += htmltext('</div>')
            r += htmltext('<div class="big-msg-info">')
            r += htmltext('<p>%s</p>') % _(
                'This site is currently empty.  It is required to first add forms.'
            )
            r += htmltext('</div>')
            return r.getvalue()

        get_response().filter['sidebar'] = self.get_stats_sidebar()
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Global statistics')
        r += htmltext('<div class="warningnotice"><p>%s <a href="%s">%s</a></p></div>') % (
            _('This view is deprecated and will soon be removed.'),
            'https://doc-publik.entrouvert.com/agent-traitant/statistiques-internes/#statistiques-internes',
            _('More information in documentation.'),
        )

        formdefs = FormDef.select(order_by='name', ignore_errors=True)

        counts = {}
        parsed_values = {}
        criterias = get_global_criteria(get_request(), parsed_values)
        period_start = parsed_values.get('period_start')
        period_end = parsed_values.get('period_end')

        from wcs import sql

        formdef_totals = sql.get_formdef_totals(period_start, period_end, criterias)
        counts = {x: y for x, y in formdef_totals}

        r += htmltext('<p>%s %s</p>') % (_('Total count:'), sum(counts.values()))

        r += htmltext('<div class="splitcontent-left">')
        cats = Category.select()
        for cat in cats:
            if not cat.has_permission('statistics', get_request().user):
                continue
            category_formdefs = [x for x in formdefs if x.category_id == str(cat.id)]
            r += self.category_global_stats(cat.name, category_formdefs, counts)

        category_formdefs = [x for x in formdefs if x.category_id is None]
        r += self.category_global_stats(_('Misc'), category_formdefs, counts)

        r += htmltext('</div>')
        r += htmltext('<div class="splitcontent-right">')
        r += do_graphs_section(period_start, period_end, criterias=[StrictNotEqual('status', 'draft')])
        r += htmltext('</div>')

        return r.getvalue()

    def category_global_stats(self, title, category_formdefs, counts):
        r = TemplateIO(html=True)
        category_formdefs_ids = [x.id for x in category_formdefs]
        if not category_formdefs:
            return
        cat_counts = {x: y for x, y in counts.items() if x in category_formdefs_ids}
        if sum(cat_counts.values()) == 0:
            return
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h3>%s</h3>') % title
        r += htmltext('<p>%s %s</p>') % (_('Count:'), sum(cat_counts.values()))
        r += htmltext('<ul>')
        for category_formdef in category_formdefs:
            if not counts.get(category_formdef.id):
                continue
            r += htmltext('<li>%s %s</li>') % (
                _('%s:') % category_formdef.name,
                counts.get(category_formdef.id),
            )
        r += htmltext('</ul>')
        r += htmltext('</div>')
        return r.getvalue()

    def display_forms(self, forms_list):
        r = TemplateIO(html=True)
        cats = Category.select()
        Category.sort_by_position(cats)
        for c in cats + [None]:
            if c is None:
                l2 = [x for x in forms_list if not x[0].category_id]
                cat_name = _('Misc')
            else:
                l2 = [x for x in forms_list if x[0].category_id == c.id]
                cat_name = c.name
            if not l2:
                continue
            if c is None:
                r += htmltext('<div class="section">')
            else:
                folded_pref_name = f'folded-data-forms-category-{c.id}'
                classes = 'section foldable'
                if get_request().user.get_preference(folded_pref_name):
                    classes += ' folded'
                r += htmltext('<div class="%s" data-section-folded-pref-name="%s">') % (
                    classes,
                    folded_pref_name,
                )
            r += htmltext('<h3>%s</h3>') % cat_name
            r += htmltext('<ul class="objects-list single-links">')
            for formdef, no_pending, no_total in l2:
                r += htmltext('<li><a href="%s/">%s') % (formdef.url_name, formdef.name)
                r += htmltext('<span class="badge">')
                if no_pending:
                    r += str(_('%(pending)s open on %(total)s') % {'pending': no_pending, 'total': no_total})
                else:
                    r += ngettext('%(total)s item', '%(total)s items', no_total) % {'total': no_total}
                r += htmltext('</span>')
                r += htmltext('</a></li>')
            r += htmltext('</ul>')
            r += htmltext('</div>')

        return r.getvalue()

    def get_global_listing_criterias(self, ignore_user_roles=False):
        parsed_values = {}
        user_roles = [logged_users_role().id]
        if get_request().user:
            user_roles.extend(get_request().user.get_roles())
        criterias = get_global_criteria(get_request(), parsed_values)
        query_parameters = (get_request().form or {}).copy()
        query_parameters.pop('callback', None)  # when using jsonp
        status = query_parameters.get('status', 'waiting')
        if query_parameters.get('waiting') == 'yes':
            # compatibility with ?waiting=yes|no parameter, still used in
            # the /count endpoint used for indicators
            status = 'waiting'
        elif query_parameters.get('waiting') == 'no':
            status = 'open'
        if status == 'waiting':
            criterias.append(Equal('is_at_endpoint', False))
            if not ignore_user_roles:
                criterias.append(Intersects('actions_roles_array', user_roles))
        elif status == 'open':
            criterias.append(Equal('is_at_endpoint', False))
            if not ignore_user_roles:
                criterias.append(Intersects('concerned_roles_array', user_roles))
        elif status == 'done':
            criterias.append(Equal('is_at_endpoint', True))
            if not ignore_user_roles:
                criterias.append(Intersects('concerned_roles_array', user_roles))
        elif status == 'all':
            if not ignore_user_roles:
                criterias.append(Intersects('concerned_roles_array', user_roles))
        else:
            raise RequestError(_('Invalid status value.'))

        name_id = query_parameters.get('filter-user-uuid')
        if name_id:
            nameid_users = get_publisher().user_class.get_users_with_name_identifier(name_id)
            if nameid_users:
                criterias.append(Equal('user_id', str(nameid_users[0].id)))
            else:
                criterias.append(Equal('user_id', None))

        if get_request().form.get('submission_channel'):
            if get_request().form.get('submission_channel') == 'web':
                criterias.append(Null('submission_channel'))
            else:
                criterias.append(Equal('submission_channel', get_request().form.get('submission_channel')))
        category_slugs = []
        category_ids = []
        if get_request().form:
            prefix = 'category_ids$element'
            category_slugs = (get_request().form.get('category_slugs') or '').split(',')
            category_slugs = [c.strip() for c in category_slugs if c.strip()]
            if category_slugs:
                category_ids = [c.id for c in Category.select() if c.url_name in category_slugs]
            else:
                category_ids = [
                    get_request().form.get(k) for k in get_request().form.keys() if k.startswith(prefix)
                ]
                category_ids = [v for v in category_ids if v and is_ascii_digit(v)]
        if category_slugs or category_ids:
            criterias.append(Contains('category_id', category_ids))
        if get_request().form.get('q'):
            criterias.append(ExtendedFtsMatch(get_request().form.get('q')))
        return criterias

    def empty_site_message(self, title):
        r = TemplateIO(html=True)
        r += htmltext('<div class="top-title">')
        r += htmltext('<h2>%s</h2>') % title
        r += htmltext('</div>')
        r += htmltext('<div class="big-msg-info">')
        r += htmltext('<p>%s</p>') % _('This site is currently empty.  It is required to first add forms.')
        r += htmltext('</div>')
        return r.getvalue()

    def listing(self):
        get_response().add_javascript(['wcs.listing.js'])
        from wcs import sql

        get_response().set_title(_('Management'))

        if not (FormDef.exists()):
            return self.empty_site_message(_('Global View'))

        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size') or 20)
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        order_by = misc.get_order_by_or_400(
            get_request().form.get(
                'order_by', get_publisher().get_site_option('default-sort-order') or '-receipt_time'
            )
        )

        criterias = self.get_global_listing_criterias()
        criterias.append(Null('anonymised'))  # exclude anonymised forms
        total_count = sql.AnyFormData.count(criterias)
        if offset > total_count:
            get_request().form['offset'] = '0'
            return redirect('listing?' + urllib.parse.urlencode(get_request().form))
        formdatas = sql.AnyFormData.select(criterias, order_by=order_by, limit=limit, offset=offset)
        include_submission_channel = get_cfg('submission-channels', {}).get('include-in-global-listing')

        r = TemplateIO(html=True)
        r += htmltext('<table id="listing" class="main">')
        r += htmltext('<thead><tr>')
        r += htmltext('<th data-field-sort-key="criticality_level"><span></span></th>')
        if include_submission_channel:
            r += htmltext('<th data-field-sort-key="submission_channel"><span>%s</span></th>') % _('Channel')
        r += htmltext('<th data-field-sort-key="formdef_name"><span>%s</span></th>') % _('Form')
        r += htmltext('<th><span>%s</span></th>') % _('Reference')
        r += htmltext('<th data-field-sort-key="receipt_time"><span>%s</span></th>') % _('Created')
        r += htmltext('<th data-field-sort-key="last_update_time"><span>%s</span></th>') % _('Last Modified')
        r += htmltext('<th data-field-sort-key="user_name"><span>%s</span></th>') % pgettext_lazy(
            'frontoffice', 'User'
        )
        r += htmltext('<th class="nosort"><span>%s</span></th>') % _('Status')
        r += htmltext('</tr></thead>')
        r += htmltext('<tbody>')
        workflows = {}
        session = get_session()
        visited_objects = session.get_visited_objects(exclude_user=session.user)
        for formdata in formdatas:
            if formdata.formdef.workflow_id not in workflows:
                workflows[formdata.formdef.workflow_id] = formdata.formdef.workflow

            classes = ['status-%s-%s' % (formdata.formdef.workflow.id, formdata.status)]
            if formdata.get_object_key() in visited_objects:
                classes.append('advisory-lock')
            if formdata.backoffice_submission:
                classes.append('backoffice-submission')
            style = ''
            try:
                level = formdata.get_criticality_level_object()
            except IndexError:
                pass
            else:
                classes.append('criticality-level')
                style = 'style="border-left-color: %s;"' % level.colour
            url = formdata.get_url(backoffice=True) + '?origin=global'
            r += htmltext('<tr class="%s" data-link="%s">' % (' '.join(classes), url))
            r += htmltext('<td %s></td>' % style)  # lock
            if include_submission_channel:
                r += htmltext('<td>%s</td>') % formdata.get_submission_channel_label()
            r += htmltext('<td>%s') % formdata.formdef.name
            if formdata.default_digest:
                r += htmltext(' <small>%s</small>') % formdata.default_digest
            r += htmltext('</td>')
            r += htmltext('<td><a href="%s">%s</a></td>') % (url, formdata.get_display_id())
            r += htmltext('<td class="cell-time">%s</td>') % misc.localstrftime(formdata.receipt_time)
            r += htmltext('<td class="cell-time">%s</td>') % misc.localstrftime(formdata.last_update_time)
            value = formdata.get_user_label()
            if value:
                r += htmltext('<td class="cell-user">%s</td>') % value
            else:
                r += htmltext('<td class="cell-user cell-no-user">-</td>')
            r += htmltext('<td class="cell-status">%s</td>') % formdata.get_status_label()
            r += htmltext('</tr>\n')

        if workflows:
            colours = []
            for workflow in workflows.values():
                for status in workflow.possible_status:
                    if status.colour and status.colour != '#FFFFFF':
                        fg_colour = misc.get_foreground_colour(status.colour)
                        colours.append((workflow.id, status.id, status.colour, fg_colour))
            if colours:
                r += htmltext('<style>')
                for workflow_id, status_id, bg_colour, fg_colour in colours:
                    r += htmltext(
                        'tr.status-%s-wf-%s td.cell-status { '
                        'background-color: %s !important; color: %s !important; }\n'
                        % (workflow_id, status_id, bg_colour, fg_colour)
                    )
                r += htmltext('</style>')
        r += htmltext('</tbody></table>')

        if (offset > 0) or (total_count > limit > 0):
            r += pagination_links(offset, limit, total_count)

        if get_query_flag('ajax'):
            get_request().ignore_session = True
            get_response().raw = True
            return r.getvalue()

        get_response().filter['sidebar'] = self.get_global_listing_sidebar(
            limit=limit, offset=offset, order_by=order_by, view='listing'
        )
        rt = TemplateIO(html=True)
        rt += htmltext('<div id="appbar">')
        rt += htmltext('<h2>%s</h2>') % _('Global View')
        rt += htmltext('<span class="actions">')
        rt += htmltext('<a href="forms">%s</a>') % _('Forms View')
        for formdef in FormDef.select(lightweight=True):
            if formdef.geolocations:
                url = 'map'
                if get_request().get_query():
                    url += '?' + get_request().get_query()
                rt += htmltext(' <a data-base-href="map" href="%s">' % url)
                rt += htmltext('%s</a>') % _('Map View')
                break
        rt += htmltext('</span>')
        rt += htmltext('</div>')
        rt += get_session().display_message()
        rt += r.getvalue()
        r = rt
        return rt.getvalue()

    def count(self):
        if not (FormDef.exists()):
            return misc.json_response({'count': 0})
        from wcs import sql

        criterias = self.get_global_listing_criterias()
        count = sql.AnyFormData.count(criterias)
        return misc.json_response({'count': count})

    def geojson(self):
        from wcs import sql

        criterias = self.get_global_listing_criterias()
        formdatas = sql.AnyFormData.select(
            criterias + [NotNull('geoloc_base_x'), Null('anonymised')], iterator=True, itersize=4096
        )
        fields = [
            filter_fields.DisplayNameFilterField(formdef=None),
            filter_fields.StatusFilterField(formdef=None),
        ]
        get_response().set_content_type('application/json')
        return json.dumps(geojson_formdatas(formdatas, fields=fields), cls=misc.JSONEncoder)

    def map(self):
        get_response().add_javascript(['wcs.listing.js', 'qommon.map.js'])
        get_response().set_title(_('Global Map'))
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('map', _('Global Map')))
        attrs = {
            'class': 'qommon-map',
            'id': 'backoffice-map',
            'data-readonly': True,
            'data-geojson-url': '%s/geojson?%s' % (get_request().get_url(1), get_request().get_query()),
        }
        attrs.update(get_publisher().get_map_attributes())

        get_response().filter['sidebar'] = self.get_global_listing_sidebar(view='map')

        if not get_query_flag('ajax'):
            r += htmltext('<div id="appbar">')
            r += htmltext('<h2>%s</h2>') % _('Global Map')
            r += htmltext('<span class="actions">')
            r += htmltext('<a data-base-href="listing" href="listing">%s</a>') % _('Global View')
            r += htmltext('<a href="forms">%s</a>') % _('Forms View')
            r += htmltext('</span>')
            r += htmltext('</div>')

        r += htmltext('<div><div %s></div></div>' % ' '.join(['%s="%s"' % x for x in attrs.items()]))
        return r.getvalue()

    def _q_lookup(self, component):
        return FormPage(component)


class FormPage(Directory, TempfileDirectoryMixin):
    do_not_call_in_templates = True
    _q_exports = [
        '',
        'live',
        'csv',
        'stats',
        'ods',
        'json',
        'export',
        'map',
        'geojson',
        'actions',
        ('export-spreadsheet', 'export_spreadsheet'),
        ('filter-options', 'filter_options'),
        ('save-view', 'save_view'),
        ('delete-view', 'delete_view'),
        ('view-settings', 'view_settings'),
    ]
    _view = None
    use_default_view = False
    has_json_export_support = False
    admin_permission = 'forms'
    formdef_class = FormDef
    export_data_label = _('Export a Spreadsheet')
    search_label = _('Search in form content')
    formdef_view_label = _('View Form')
    WCS_SYNC_EXPORT_LIMIT = 100  # Arbitrary threshold
    view_type = None

    def __init__(self, component=None, formdef=None, view=None, update_breadcrumbs=True):
        if component:
            try:
                self.formdef = self.formdef_class.get_by_urlname(component)
            except KeyError:
                raise errors.TraversalError()
            if update_breadcrumbs:
                get_response().breadcrumb.append((component + '/', self.formdef.name))
        else:
            self.formdef = formdef
            self._view = view
            if update_breadcrumbs:
                get_response().breadcrumb.append((view.get_url_slug() + '/', view.title))
        from wcs.forms.actions import ActionsDirectory

        self.actions = ActionsDirectory()

    _default_view = Ellipsis  # unset

    @property
    def default_view(self):
        if self._default_view is not Ellipsis:
            return self._default_view

        self._default_view = None
        if not get_request():
            return
        custom_views = list(
            self.get_custom_views(
                [
                    Equal('is_default', True),
                    Contains('visibility', ['any', 'role', 'owner']),
                ]
            )
        )

        # search for first default user custom view
        for view in custom_views:
            if view.visibility != 'owner':
                continue
            self._default_view = view
            return self._default_view

        # search for first default role custom view
        user = get_request().user
        if user:
            user_role_ids = user.get_roles()
            for view in custom_views:
                if view.visibility != 'role':
                    continue
                if view.role_id in user_role_ids:
                    self._default_view = view
                    return self._default_view

        # default user custom view not found, search in 'any' custom views
        for view in custom_views:
            if view.visibility != 'any':
                continue
            self._default_view = view
            return self._default_view

    @property
    def view(self):
        view = self._view
        if self.use_default_view:
            view = view or self.default_view
        return view

    def check_access(self, api_name=None):
        session = get_session()
        user = get_request().user
        if user is None and not (get_publisher().user_class.exists()):
            user = get_publisher().user_class()
            user.is_admin = True
        if not user:
            raise errors.AccessUnauthorizedError()
        if not user.is_admin and not self.formdef.is_of_concern_for_user(user):
            if session.user:
                raise errors.AccessForbiddenError()
            raise errors.AccessUnauthorizedError()

    def get_custom_views(self, criterias=None):
        criterias = [
            Equal('formdef_type', self.formdef.xml_root_node),
            Equal('formdef_id', str(self.formdef.id)),
        ] + (criterias or [])
        for view in get_publisher().custom_view_class.select(clause=criterias, order_by='id'):
            if view.match(get_request().user, self.formdef):
                yield view

    def get_formdata_sidebar_actions(self, qs=''):
        r = TemplateIO(html=True)
        if not self.formdef.category or self.formdef.category.has_permission('export', get_request().user):
            r += htmltext(
                ' <li><a rel="popup" data-base-href="export-spreadsheet" data-autoclose-dialog="true" '
                'href="export-spreadsheet%s">%s</a></li>'
            ) % (qs, self.export_data_label)
        if self.formdef.geolocations and not self.view_type == 'map':
            r += htmltext(' <li><a data-base-href="map" href="map%s">%s</a></li>') % (qs, _('Plot on a Map'))
        elif self.view_type == 'map':
            r += htmltext(' <li><a data-base-href="./" href="./%s">%s</a></li>') % (qs, _('Management view'))
        if (
            'stats' in self._q_exports
            and not get_publisher().has_site_option('disable-internal-statistics')
            and (
                not self.formdef.category
                or self.formdef.category.has_permission('statistics', get_request().user)
            )
        ):
            r += htmltext(' <li class="stats"><a href="stats">%s</a></li>') % _('Statistics')

        if self.formdef.has_admin_access(get_request().user):
            r += htmltext(' <li><a href="%s">%s</a></li>') % (
                self.formdef.get_admin_url(),
                self.formdef_view_label,
            )
        if self.formdef.workflow.has_admin_access(get_request().user):
            r += htmltext(' <li><a href="%s">%s</a></li>') % (
                self.formdef.workflow.get_admin_url(),
                _('View Workflow'),
            )
        return r.getvalue()

    def get_formdata_sidebar(self, qs=''):
        r = TemplateIO(html=True)
        r += htmltext('<ul id="sidebar-actions">')
        r += self.get_formdata_sidebar_actions(qs=qs)
        r += htmltext('</ul>')
        criterias = []
        if not self.formdef.has_admin_access(get_request().user):
            criterias = [NotEqual('visibility', 'datasource')]
        views = list(self.get_custom_views(criterias))
        if views:
            r += htmltext('<h3>%s</h3>') % _('Custom Views')
            r += htmltext('<ul class="sidebar-custom-views">')
            view_type = 'map' if self.view_type == 'map' else ''
            datasource_views = []
            for view in sorted(views, key=lambda x: getattr(x, 'title')):
                if view.visibility == 'datasource':
                    datasource_views.append(view)
                    continue
                if self._view:
                    active = bool(self._view.get_url_slug() == view.get_url_slug())
                    r += htmltext('<li class="active">' if active else '<li>')
                    r += htmltext('<a href="../%s/%s">%s</a>') % (view.get_url_slug(), view_type, view.title)
                else:
                    r += htmltext('<li><a href="%s/%s">%s</a>') % (view.get_url_slug(), view_type, view.title)
                if self.default_view and view.id == self.default_view.id:
                    r += htmltext(' <span class="default-custom-view">(%s)</span>') % _('default')
                r += htmltext('</li>')
            r += htmltext('</ul>')
            if datasource_views:
                klass = 'folded'
                if self._view and any(
                    bool(self._view.get_url_slug() == x.get_url_slug()) for x in datasource_views
                ):
                    # current active view is a datasource, do not fold
                    klass = ''
                r += htmltext(f'<fieldset class="sidebar-custom-datasource-views foldable {klass}">')
                r += htmltext('<legend>%s</legend>') % _('Data sources')
                r += htmltext('<ul class="sidebar-custom-views">')
                for view in datasource_views:
                    if self._view:
                        active = bool(self._view.get_url_slug() == view.get_url_slug())
                        r += htmltext('<li class="active">' if active else '<li>')
                        r += htmltext('<a href="../%s/%s">%s</a>') % (
                            view.get_url_slug(),
                            view_type,
                            view.title,
                        )
                    else:
                        r += htmltext('<li><a href="%s/%s">%s</a>') % (
                            view.get_url_slug(),
                            view_type,
                            view.title,
                        )
                    r += htmltext('</li>')
                r += htmltext('</ul></fieldset>')
        return r.getvalue()

    def get_default_filters(self, mode):
        if self.view:
            return self.view.get_default_filters()
        if mode == 'listing':
            # enable status filter by default
            return ('status',)
        if mode == 'stats':
            # enable period filters by default
            return ('start', 'end')
        return ()

    def get_item_filter_options(
        self,
        filter_field,
        selected_filter,
        selected_filter_operator='eq',
        criterias=None,
        anonymised=False,
    ):
        if (self.view_type != 'json' and self.view and self.view.visibility == 'datasource') or (
            filter_field.items and not filter_field.data_source
        ):
            return filter_field.get_options()
        # remove potential filter on self
        filter_field_id = get_field_id(filter_field)
        filtered_criterias = []
        for criteria in criterias or []:
            if getattr(criteria, 'attribute', None) == filter_field_id:
                continue
            if isinstance(criteria, Not) and getattr(criteria.criteria, 'attribute', None) == filter_field_id:
                continue
            if isinstance(criteria, Nothing):
                continue
            filtered_criterias.append(criteria)
        criterias = filtered_criterias
        # apply other filters
        if not anonymised:
            criterias.append(Null('anonymised'))
        criterias.append(StrictNotEqual('status', 'draft'))
        criterias += FormDefUI(self.formdef).get_status_criterias(
            selected_filter, selected_filter_operator, get_request().user
        )

        from wcs import sql

        # for item/items fields, get actual option values from database
        if not getattr(filter_field, 'block_field', None):
            criterias.append(NotNull(sql.get_field_id(filter_field)))
            options = self.formdef.data_class().select_distinct(
                [sql.get_field_id(filter_field), '%s_display' % sql.get_field_id(filter_field)],
                clause=criterias,
            )
        else:
            # in case of blocks, this requires digging into the jsonb columns,
            # jsonb_array_elements(BLOCK->'data')->> 'FOOBAR' will return all
            # values used in repeated blocks, ex:
            # {"data": [{"FOOBAR": "value1"}, {"FOOBAR": "value2}]}
            # → ["value1", "value2"}
            field1 = "jsonb_array_elements(%s->'data')-> '%s'" % (
                sql.get_field_id(filter_field.block_field),
                filter_field.id,
            )
            field2 = "jsonb_array_elements(%s->'data')->> '%s_display'" % (
                sql.get_field_id(filter_field.block_field),
                filter_field.id,
            )
            options = self.formdef.data_class().select_distinct(
                [field1, field2], clause=criterias, first_field_alias='_fid'
            )

        if filter_field.key == 'item':
            options = list(sorted(filter_field.get_filter_options(options), key=lambda x: (x[1] or '')))
        elif filter_field.key == 'items':
            options = list(sorted(filter_field.get_exploded_options(options), key=lambda x: (x[1] or '')))

        options = [(force_str(x), force_str(y)) for x, y in options if x and y]
        options.sort(key=lambda x: misc.simplify(x[1]))

        return options

    def get_item_filter_options_text_values(
        self,
        filter_field,
        filter_field_value,
    ):
        if Template.is_template_string(filter_field_value, ezt_support=False) or getattr(
            filter_field, 'block_field', None
        ):
            # do not return anything for template values, or block fields
            return {}
        criterias = [Null('anonymised'), StrictNotEqual('status', 'draft')]

        filter_field_values = [x for x in filter_field_value.split('|') if x]
        criterias.append(Contains(f'f{filter_field.id}', filter_field_values))

        from wcs import sql

        criterias.append(NotNull(sql.get_field_id(filter_field)))
        options = self.formdef.data_class().select_distinct(
            [sql.get_field_id(filter_field), '%s_display' % sql.get_field_id(filter_field)],
            clause=criterias,
        )

        options = list(sorted(filter_field.get_filter_options(options), key=lambda x: (x[1] or '')))

        return {force_str(x): force_str(y) for x, y in options if x and y}

    def filter_options(self):
        get_request().is_json_marker = True
        field_id = get_request().form.get('filter_field_id')
        for filter_field in self.get_formdef_fields():
            if filter_field.key not in ('item', 'items'):
                continue
            if filter_field.contextual_id == field_id:
                break
            if getattr(filter_field, 'contextual_varname', None) == field_id:
                break
        else:
            raise errors.TraversalError()

        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        criterias = self.get_criterias_from_query()
        options = self.get_item_filter_options(
            filter_field,
            selected_filter,
            selected_filter_operator,
            criterias,
        )
        if '_search' in get_request().form:  # select2
            term = get_request().form.get('_search')
            if term:
                options = [x for x in options if term.lower() in x[1].lower()]
            options = options[:15]
        if self.view_type != 'json' and self.view and self.view.visibility == 'datasource':
            options.append(('{}', _('custom value')))
        get_response().set_content_type('application/json')
        return json.dumps(
            {'err': 0, 'data': [{'id': x[0], 'text': x[1]} for x in options]}, cls=misc.JSONEncoder
        )

    def get_filter_sidebar(
        self,
        selected_filter=None,
        selected_filter_operator='eq',
        mode='listing',
        query=None,
        criterias=None,
    ):
        r = TemplateIO(html=True)

        fake_fields = [
            klass(formdef=self.formdef)
            for klass in (
                filter_fields.InternalIdFilterField,
                filter_fields.PeriodStartFilterField,
                filter_fields.PeriodEndFilterField,
                filter_fields.UserIdFilterField,
                filter_fields.UserFunctionFilterField,
                filter_fields.CriticalityLevelFilterFiled,
            )
        ]
        default_filters = self.get_default_filters(mode)

        state_dict = copy.copy(self.get_view_state_dict())

        available_fields = []
        for field in fake_fields + list(self.get_formdef_fields()):
            field.enabled = False
            field.formdef = self.formdef
            if not field.available_for_filter:
                continue
            available_fields.append(field)

            if state_dict:
                field.enabled = ('filter-%s' % field.contextual_id in state_dict) or (
                    'filter-%s' % field.contextual_varname in state_dict
                )

                if 'filter-%s' % field.contextual_varname in state_dict and (
                    'filter-%s-value' % field.contextual_varname not in state_dict
                ):
                    # if ?filter-<varname>= is used, take the value and put it
                    # into filter-<field id>-value so it is used to fill the
                    # fields.
                    state_dict['filter-%s-value' % field.contextual_id] = state_dict.get(
                        'filter-%s' % field.contextual_varname
                    )
                    if (
                        field.contextual_varname in ('start', 'end', 'user', 'submission-agent')
                        and state_dict['filter-%s-value' % field.contextual_id] == 'on'
                    ):
                        # reset start/end to an empty value when they're just
                        # being enabled
                        state_dict['filter-%s-value' % field.contextual_id] = ''
                if 'filter-%s-operator' % field.contextual_varname in state_dict and (
                    'filter-%s-operator' % field.contextual_id not in state_dict
                ):
                    # init filter-<field id>-operator with filter-<varname>-operator
                    state_dict['filter-%s-operator' % field.contextual_id] = state_dict.get(
                        'filter-%s-operator' % field.contextual_varname
                    )
                if not field.enabled and self.view and state_dict.get('keep-view-filters'):
                    # keep-view-filters=on is used to initialize page with
                    # filters from both the custom view and the query string.
                    field.enabled = field.contextual_id in default_filters
            else:
                field.enabled = field.contextual_id in default_filters
                if not self.view and field.key in ('item', 'items'):
                    field.enabled = field.in_filters

        r += htmltext('<h3><span>%s</span>') % _('Current view')
        r += htmltext('<span class="change">(')
        r += htmltext('<a id="filter-settings">%s</a>') % _('filters')
        if self.view_type in ('table', 'map'):
            if self.view_type == 'table':
                columns_settings_labels = (_('Columns Settings'), _('columns'))
            elif self.view_type == 'map':
                columns_settings_labels = (_('Marker Settings'), _('markers'))
            r += htmltext(' - <a id="columns-settings" title="%s">%s</a>') % columns_settings_labels
        r += htmltext(')</span></h3>')

        filters_dict = {}
        if self.view:
            filters_dict.update(self.view.get_filters_dict())
        filters_dict.update(state_dict)

        if selected_filter:
            filters_dict['filter-status-value'] = selected_filter
            filters_dict['filter-status-operator'] = selected_filter_operator

        def render_widget(filter_widget, operators):
            return filter_fields.render_filter_widget(
                filter_widget, operators, filter_field_operator_key, filter_field_operator
            )

        for filter_field in available_fields:
            if not filter_field.enabled:
                continue

            filter_field.filters_dict = filters_dict

            filter_field_key = 'filter-%s-value' % filter_field.contextual_id
            filter_field_value = filters_dict.get(filter_field_key)

            filter_field_operator_key = '%s-operator' % filter_field_key.replace('-value', '')
            filter_field_operator = filters_dict.get(filter_field_operator_key) or 'eq'

            lazy_manager = LazyFormDefObjectsManager(formdef=self.formdef)
            operators = lazy_manager.get_field_allowed_operators(filter_field) or []

            if hasattr(filter_field, 'get_filter_widget'):
                r += filter_field.get_filter_widget(mode=mode)

            elif filter_field.key in ('item', 'items'):
                filter_field.required = False

                # Get options from existing formdatas, except for custom views with visibility "datasource"
                # This allows for options that don't appear anymore in the
                # data source to be listed (for example because the field
                # is using a parametrized URL depending on unavailable
                # variables, or simply returning different results now).
                is_datasource_customview = self.view and self.view.visibility == 'datasource'

                display_mode = 'select'
                if (
                    not is_datasource_customview
                    and filter_field.key == 'item'
                    and filter_field.get_display_mode() == 'autocomplete'
                ):
                    display_mode = 'select2'
                if is_datasource_customview:
                    options = filter_field.get_options()
                    if len(options) > 15:
                        display_mode = 'select2'

                is_multi_values = filter_field_operator in ['in', 'not_in', 'between']
                if display_mode == 'select':
                    if not is_datasource_customview:
                        options = self.get_item_filter_options(
                            filter_field,
                            selected_filter,
                            selected_filter_operator,
                            criterias,
                        )
                        options = [(x[0], x[1], x[0]) for x in options]
                    options.insert(0, (None, '', ''))
                    attrs = {'data-refresh-options': str(filter_field.contextual_id)}
                else:
                    options = [(None, '', '')]
                    if not is_multi_values:
                        value_display = filter_field_value or ''
                        if filter_field_value:
                            value_display = (
                                filter_field.get_display_value(filter_field_value) or filter_field_value
                            )
                        options = [(filter_field_value, value_display, filter_field_value or '')]
                    attrs = {'data-remote-options': str(filter_field.contextual_id)}
                    get_response().add_javascript(
                        ['jquery.js', '../../i18n.js', 'qommon.forms.js', 'select2.js']
                    )
                    get_response().add_css_include('select2.css')
                if is_datasource_customview:
                    options.append(('{}', _('custom value'), '{}'))
                    if filter_field_value and filter_field_value not in [x[0] for x in options]:
                        options.append((filter_field_value, filter_field_value, filter_field_value))
                    attrs['data-allow-template'] = 'true'
                if is_multi_values:
                    attrs['data-multi-values'] = filter_field_value
                    if display_mode == 'select2' and filter_field.key == 'item' and filter_field_value:
                        # add all options to a json object that will be used to fill
                        # <option> texts.
                        d = self.get_item_filter_options_text_values(filter_field, filter_field_value)
                        r += htmltext(
                            f'<script id="filter-options-{filter_field_key}" type="application/json">'
                        )
                        r += htmltext(html.escape(json.dumps(d), quote=False))
                        r += htmltext('</script>')

                widget = SingleSelectWidget(
                    filter_field_key,
                    title=get_field_criteria_label(filter_field),
                    options=options,
                    value=filter_field_value,
                    render_br=False,
                    attrs=attrs,
                )
                r += render_widget(widget, operators)

            elif filter_field.key == 'bool':
                options = [(None, '', ''), (True, _('Yes'), 'true'), (False, _('No'), 'false')]
                if filter_field_value == 'true':
                    filter_field_value = True
                elif filter_field_value == 'false':
                    filter_field_value = False
                widget = SingleSelectWidget(
                    filter_field_key,
                    title=get_field_criteria_label(filter_field),
                    options=options,
                    value=filter_field_value,
                    render_br=False,
                )
                r += render_widget(widget, operators)

            elif filter_field.key in ('string', 'text', 'email', 'numeric', 'date'):
                widget = StringWidget(
                    filter_field_key,
                    title=get_field_criteria_label(filter_field),
                    value=filter_field_value,
                    render_br=False,
                )
                r += render_widget(widget, operators)

        # field filter dialog content
        r += htmltext('<div style="display: none;">')
        r += htmltext('<ul id="field-filter" class="objects-list">')
        for field in available_fields:
            r += htmltext('<li>')
            r += htmltext('<label for="fields-filter-%s">') % field.contextual_id
            r += htmltext('<input type="checkbox" name="filter-%s"') % field.contextual_id
            if field.enabled:
                r += htmltext(' checked="checked"')
            r += htmltext(' id="fields-filter-%s"') % field.contextual_id
            r += htmltext('/>%s</label>') % get_field_selection_label(field)
            r += htmltext('</li>')
        r += htmltext('</ul>')
        r += htmltext('</div>')

        return r.getvalue()

    def get_default_order_by(self, system_default_order_by='-receipt_time'):
        default_order_by = get_publisher().get_site_option('default-sort-order') or system_default_order_by
        if self.view:
            default_order_by = self.view.order_by or default_order_by
        return default_order_by

    def get_detailed_order_by_from_query(self, system_default_order_by='-receipt_time'):
        # return (is_default_order_by, order_by_value)
        order_by = misc.get_order_by_or_400(get_request().form.get('order_by'))
        default_order_by = self.get_default_order_by(system_default_order_by=system_default_order_by)

        if get_request().form.get('q') and not order_by:
            return (True, 'rank')
        if not order_by:
            return (True, default_order_by)

        return (False, order_by)

    def get_order_by_from_query(self, system_default_order_by='-receipt_time'):
        return self.get_detailed_order_by_from_query(system_default_order_by=system_default_order_by)[1]

    def get_fields_sidebar(
        self,
        selected_filter,
        selected_filter_operator,
        fields,
        offset=None,
        limit=None,
        order_by=None,
        query=None,
        criterias=None,
        action='.',
    ):
        get_response().add_javascript(['wcs.listing.js'])

        r = TemplateIO(html=True)
        r += htmltext('<form id="listing-settings" method="post" action="view-settings">')
        r += htmltext('<input type="hidden" name="action" value="%s"/>') % action
        if offset or limit:
            if not offset:
                offset = 0
            r += htmltext('<input type="hidden" name="offset" value="%s"/>') % offset
        if limit:
            r += htmltext('<input type="hidden" name="limit" value="%s"/>') % limit

        if not order_by or order_by == self.get_default_order_by():
            order_by = ''
        r += htmltext('<input type="hidden" name="order_by" value="%s"/>') % order_by

        r += htmltext('<h3>%s</h3>') % self.search_label
        if get_request().form.get('q'):
            q = force_str(get_request().form.get('q'))
            r += htmltext('<input class="inline-input" name="q" value="%s">') % force_str(q)
        else:
            r += htmltext('<input class="inline-input" name="q">')
        r += htmltext('<button class="side-button">%s</button>') % _('Search')

        r += self.get_filter_sidebar(
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            query=query,
            criterias=criterias,
        )

        r += htmltext('<button class="submit-button" hidden>%s</button>') % _('Submit')

        if self.view_type in ('table', 'map'):
            # column settings dialog content
            r += htmltext('<div style="display: none;">')
            r += htmltext('<div id="columns-filter">')
            if get_request().form.get('columns-order') == '':  # present but empty
                style = ''
            else:
                style = 'display: none;'
            r += htmltext('<div class="columns-default-value-message infonotice" style="%s">' % style)
            r += htmltext('<p>%s</p>') % _('When nothing is checked the default settings will apply.')
            r += htmltext('</div>')
            r += htmltext('<ul id="columns-filter" class="objects-list columns-filter">')
            column_order = []
            field_ids = [x.contextual_id for x in fields]

            def get_column_position(x):
                if x.contextual_id in field_ids:
                    return field_ids.index(x.contextual_id)
                return 9999

            seen_parents = set()
            for field in sorted(self.get_formdef_fields(), key=get_column_position):
                if not field.can_include_in_listing:
                    continue
                classnames = ''
                attrs = ''
                if getattr(field, 'has_relations', False):
                    classnames = 'has-relations-field'
                    attrs = 'data-field-id="%s"' % field.id
                    seen_parents.add(field.id)
                elif isinstance(field, (filter_fields.RelatedField, filter_fields.CardIdField)):
                    classnames = 'related-field'
                    if field.parent_field_id in seen_parents:
                        classnames += ' collapsed'
                    attrs = 'data-relation-attr="%s"' % field.parent_field_id
                r += htmltext('<li class="%s" %s><span class="handle">⣿</span>' % (classnames, attrs))
                r += htmltext('<label><input type="checkbox" name="%s"') % field.contextual_id
                if field.contextual_id in field_ids:
                    r += htmltext(' checked="checked"')
                r += htmltext('/>')
                r += get_field_selection_label(field)
                r += htmltext('</label>')
                if getattr(field, 'has_relations', False):
                    r += htmltext('<button class="expand-relations"></button>')
                r += htmltext('</li>')
                if field.contextual_id in field_ids:
                    column_order.append(str(field.contextual_id))
            r += htmltext('</ul>')
            r += htmltext('</div>')  # </div id="columns-filter">
            r += htmltext('</div>')  # </div style="display: none">
            r += htmltext('<input type="hidden" name="columns-order" value="%s">' % ','.join(column_order))
        r += htmltext('</form>')

        r += self.get_custom_view_form().render()
        r += htmltext('<button id="save-view">%s</button>') % _('Save')
        if self.can_delete_view():
            r += htmltext(' <a data-popup id="delete-view" href="./delete-view" class="button">%s</a>') % _(
                'Delete'
            )

        return r.getvalue()

    def get_custom_view_form(self):
        form = Form(method='post', id='save-custom-view', hidden='hidden', action='save-view')
        state_dict = self.get_view_state_dict()
        form.add(HiddenWidget, 'qs', value=urllib.parse.urlencode(state_dict))
        form.add(
            StringWidget,
            'title',
            title=_('Title'),
            required=True,
            value=self.view.title if self.view else None,
        )
        can_update = False
        if self.formdef.has_admin_access(get_request().user):
            # admins can create views accessible to roles or any users
            options = [
                ('owner', _('to me only'), 'owner'),
                ('role', _('to role'), 'role'),
                ('any', _('to any users'), 'any'),
            ]
            can_update = True

            if isinstance(self.formdef, CardDef) and self.formdef.default_digest_template:
                options.append(('datasource', _('as data source'), 'datasource'))

            form.add(
                RadiobuttonsWidget,
                'visibility',
                title=_('Visibility'),
                value=self.view.visibility if self.view else 'owner',
                options=options,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
                required=True,
            )

            role_options = [(None, '---', None)]
            role_options.extend(get_user_roles())
            form.add(
                SingleSelectWidget,
                'role',
                title=_('Role'),
                value=self.view.role_id if self.view else None,
                options=role_options,
                attrs={
                    'data-dynamic-display-child-of': 'visibility',
                    'data-dynamic-display-value-in': 'role',
                },
            )
            form.add(
                CheckboxWidget,
                'is_default',
                title=_('Set as default view'),
                value=self.view.is_default if self.view else False,
                attrs={
                    'data-dynamic-display-child-of': 'visibility',
                    'data-dynamic-display-value-in': 'owner|role|any',
                },
            )
            if isinstance(self.formdef, CardDef):
                digest_template = None
                if self.view:
                    templates = self.formdef.digest_templates or {}
                    digest_template = templates.get('custom-view:%s' % self.view.get_url_slug())
                form.add(
                    StringWidget,
                    'digest_template',
                    title=_('Digest'),
                    value=digest_template or self.formdef.default_digest_template,
                    size=50,
                    attrs={
                        'data-dynamic-display-child-of': 'visibility',
                        'data-dynamic-display-value-in': 'datasource|any',
                    },
                )
                item_fields = [x for x in self.formdef.get_all_fields() if x.key == 'item']
                if item_fields:
                    form.add(
                        SingleSelectWidget,
                        'group_by',
                        title=_('Group by'),
                        value=self.view.group_by if self.view else None,
                        options=[(None, '', '')] + [(x.id, x.label, x.id) for x in item_fields],
                        attrs={
                            'data-dynamic-display-child-of': 'visibility',
                            'data-dynamic-display-value': 'datasource',
                        },
                    )
        else:
            user_roles = get_request().user.get_roles()
            static_function_role_ids = [
                x for x in (self.formdef.workflow_roles or {}).values() if x in user_roles
            ]
            static_function_roles = get_publisher().role_class.select(
                [Contains('id', static_function_role_ids)]
            )
            if static_function_roles:
                # users can create custom views for their roles
                options = [
                    ('owner', _('to me only'), 'owner'),
                    ('role', _('to role'), 'role'),
                ]
                form.add(
                    RadiobuttonsWidget,
                    'visibility',
                    title=_('Visibility'),
                    value=self.view.visibility if self.view else 'owner',
                    options=options,
                    attrs={'data-dynamic-display-parent': 'true'},
                    extra_css_class='widget-inline-radio',
                    required=True,
                )

                role_options = [(None, '---', None)]
                role_options.extend([(x.id, x.name, x.id) for x in static_function_roles])
                form.add(
                    SingleSelectWidget,
                    'role',
                    title=_('Role'),
                    value=self.view.role_id if self.view else None,
                    options=role_options,
                    attrs={
                        'data-dynamic-display-child-of': 'visibility',
                        'data-dynamic-display-value-in': 'role',
                    },
                )
                can_update = bool(self.view and self.view.visibility == 'role')

            form.add(
                CheckboxWidget,
                'is_default',
                title=_('Set as default view'),
                value=self.view.is_default if self.view else False,
            )

        if self.view and (self.view.user_id == str(get_request().user.id) or can_update):
            form.add(CheckboxWidget, 'update', title=_('Update existing view settings'), value=True)
        form.add_submit('submit', _('Save View'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def save_view(self):
        form = self.get_custom_view_form()

        if form.get_widget('_form_id').has_error():
            get_session().add_message(_('Invalid form.'))
            return redirect('.')

        title = form.get_widget('title').parse()
        if not title:
            get_session().add_message(_('Missing title.'))
            return redirect('.')
        if form.get_widget('update') and form.get_widget('update').parse():
            custom_view = self.view
            snapshot_message = _('Change to custom view (%s)') % title
            current_visibility = self.view.visibility
        else:
            snapshot_message = _('New custom view (%s)') % title
            custom_view = get_publisher().custom_view_class()
            custom_view.author = get_request().user
            current_visibility = None
        custom_view.title = title
        custom_view.user = get_request().user
        custom_view.formdef = self.formdef
        custom_view.set_from_qs(form.get_widget('qs').parse())
        if not custom_view.columns['list']:
            get_session().add_message(_('Views must have at least one column.'))
            return redirect('.')
        if form.get_widget('is_default'):
            custom_view.is_default = form.get_widget('is_default').parse()
        if form.get_widget('visibility'):
            custom_view.visibility = form.get_widget('visibility').parse()
            if custom_view.visibility == 'datasource':
                custom_view.is_default = False
            if custom_view.visibility is None:
                get_session().add_message(_('Visibility must be set.'))
                return redirect('.')

        if custom_view.visibility == 'role':
            # Widget will be missing if view was created for role but role is
            # not allowed to manage views anymore (no statically defined function)
            # In that case the parameters are just left as is.
            if form.get_widget('role'):
                custom_view.role_id = form.get_widget('role').parse()
                if not custom_view.role_id:
                    custom_view.visibility = 'owner'
        else:
            custom_view.role_id = None
        if form.get_widget('group_by'):
            custom_view.group_by = form.get_widget('group_by').parse()
        custom_view.store()

        if custom_view.is_default and custom_view.visibility != 'datasource':
            # need to clean other views to have only one default per owner/any visibility
            for view in self.get_custom_views():
                if view.id == custom_view.id:
                    continue
                if (
                    custom_view.visibility == view.visibility
                    and view.is_default
                    and view.role_id == custom_view.role_id
                ):
                    view.is_default = False
                    view.store()

        formdef_stored = False
        if form.get_widget('digest_template') and custom_view.visibility != 'owner':
            if not self.formdef.digest_templates:
                self.formdef.digest_templates = {}
            old_value = self.formdef.digest_templates.get('custom-view:%s' % custom_view.get_url_slug())
            new_value = form.get_widget('digest_template').parse()
            if old_value != new_value:
                self.formdef.digest_templates['custom-view:%s' % custom_view.get_url_slug()] = (
                    form.get_widget('digest_template').parse()
                )
                self.formdef.store(comment=snapshot_message)
                formdef_stored = True
                if self.formdef.data_class().count():
                    from wcs.formdef_jobs import UpdateDigestAfterJob

                    get_publisher().add_after_job(UpdateDigestAfterJob(formdefs=[self.formdef]))
        elif (
            form.get_widget('digest_template')
            and custom_view.visibility == 'owner'
            and current_visibility
            and current_visibility != 'owner'
        ):
            # view was changed from a shared visibility to a private visibility, remove obsolete
            # digest template.
            old_view_digest_key = f'custom-view:{custom_view.slug}'
            if old_view_digest_key in (self.formdef.digest_templates or {}):
                del self.formdef.digest_templates[old_view_digest_key]
                self.formdef.store(comment=snapshot_message)
                formdef_stored = True
        if custom_view.visibility != 'owner':
            # store to always have a snapshot, except if owner view
            if not formdef_stored:
                # a snapshot will be stored only if there is changes
                self.formdef.store(comment=snapshot_message)

        if self.view:
            return redirect('../' + custom_view.get_url_slug() + '/')

        return redirect(custom_view.get_url_slug() + '/')

    def can_delete_view(self):
        if not self.view:
            return False
        if str(self.view.user_id) == str(get_request().user.id):
            return True
        return get_publisher().get_backoffice_root().is_accessible(self.admin_permission)

    def delete_view(self):
        if not self.can_delete_view():
            raise errors.AccessForbiddenError()
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget(
                '<p>%s</p>'
                % _('You are about to remove the \"%s\" custom view.')
                % htmlescape(self.view.title)
            )
        )
        if self.view.visibility == 'any':
            form.widgets.append(
                HtmlWidget(
                    '<div class="warningnotice"<p>%s</p></div>'
                    % _('Beware this view is available to all users, and will thus be removed for everyone.')
                )
            )
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Delete Custom View'))
            r += form.render()
            return r.getvalue()

        self.view.remove_self()
        return redirect('..')

    def get_formdef_fields(self, include_block_fields=True, include_block_items_fields=False):
        yield filter_fields.IdFilterField(formdef=self.formdef)
        if self.formdef.default_digest_template:
            yield filter_fields.DigestFilterField(formdef=self.formdef)
        yield filter_fields.SubmissionChannelFilterField(formdef=self.formdef)
        if self.formdef.backoffice_submission_roles:
            yield filter_fields.SubmissionAgentFilterField(formdef=self.formdef)
        yield filter_fields.TimeFilterField(formdef=self.formdef)
        yield filter_fields.LastUpdateFilterField(formdef=self.formdef)

        # user fields
        # user-label field but as a custom field, to get full name of user
        # using a sql join clause.
        yield filter_fields.UserLabelRelatedField()
        for field in get_publisher().user_class.get_fields():
            if not field.can_include_in_listing:
                continue
            field.has_relations = True
            yield filter_fields.UserRelatedField(field)

        for field in self.formdef.iter_fields(include_block_fields=include_block_fields):
            if getattr(field, 'block_field', None):
                if field.key == 'items' and not include_block_items_fields:
                    # not yet
                    continue
            yield field
            if field.key == 'block':
                continue
            if getattr(field, 'block_field', None):
                continue
            if not (
                field.key == 'item'
                and field.data_source
                and field.data_source.get('type', '').startswith('carddef:')
            ):
                continue
            try:
                carddef = CardDef.get_by_urlname(field.data_source['type'].split(':')[1])
            except KeyError:
                continue
            if carddef.id_template:
                field.has_relations = True
                yield filter_fields.CardIdField(field)
            for card_field in carddef.get_all_fields():
                if not card_field.can_include_in_listing:
                    continue
                field.has_relations = True
                yield filter_fields.RelatedField(carddef, card_field, field)

        yield filter_fields.StatusFilterField(formdef=self.formdef)
        if any(x.get_visibility_mode() != 'all' for x in self.formdef.workflow.possible_status):
            yield filter_fields.UserVisibleStatusField(formdef=self.formdef)
        yield filter_fields.AnonymisedFilterField(formdef=self.formdef)

    def get_default_columns(self):
        if self.view:
            field_ids = self.view.get_columns()
        else:
            field_ids = ['id', 'time', 'last_update_time', 'user-label']
            for field in self.formdef.get_all_fields():
                if field.can_include_in_listing and field.include_in_listing:
                    field_ids.append(field.id)
            field_ids.append('status')
        return field_ids

    _state_query_form = None

    def get_view_state_dict(self):
        if 'st' in get_request().form:
            if self._state_query_form:
                return self._state_query_form
            token_value = get_request().form.get('st')
            try:
                token = get_session().get_token('view-settings', token_value)
            except KeyError:
                self._state_query_form = get_request().form
                return self._state_query_form
            new_query_form = copy.copy(token.data)
            new_query_form.pop('usage')
            new_query_form.pop('session_id')
            new_query_form.update(get_request().form)
            self._state_query_form = new_query_form
            return token.data
        return get_request().form

    def get_fields_from_query(self, ignore_form=False):
        field_ids = [x for x in self.get_view_state_dict().keys()]
        if not field_ids or ignore_form:
            field_ids = self.get_default_columns()

        fields = []
        for field in self.get_formdef_fields():
            if not field.can_include_in_listing:
                # skip fields that cannot be displayed in columns (computed fields)
                continue
            if field.contextual_id in field_ids:
                fields.append(field)

        if 'columns-order' in self.get_view_state_dict() or self.view:
            if ignore_form or 'columns-order' not in self.get_view_state_dict():
                field_order = field_ids
            else:
                field_order = self.get_view_state_dict()['columns-order'].split(',')

            def field_position(x):
                if x.contextual_id in field_order:
                    return field_order.index(x.contextual_id)
                return 9999

            fields.sort(key=field_position)

        if not fields and not ignore_form:
            return self.get_fields_from_query(ignore_form=True)

        return fields

    def get_filter_from_query(self, default='waiting'):
        if 'filter' in self.get_view_state_dict():
            return self.get_view_state_dict()['filter']
        if self.view:
            view_filter = self.view.get_filter()
            if view_filter:
                return view_filter
        if self.formdef.workflow.possible_status:
            return default
        return 'all'

    def get_filter_operator_from_query(self):
        default_filter_operator = 'eq'
        if self.view:
            default_filter_operator = self.view.get_status_filter_operator()
        operator = self.get_view_state_dict().get('filter-operator') or default_filter_operator
        if operator not in ['eq', 'ne', 'in', 'not_in']:
            raise RequestError(_('Invalid operator "%s" for "filter-operator".') % operator)
        return operator

    def get_criterias_from_query(self, statistics_fields_only=False):
        query_overrides = self.get_view_state_dict()
        return self.get_view_criterias(query_overrides, statistics_fields_only=statistics_fields_only)

    def get_view_criterias(
        self,
        query_overrides=None,
        custom_view=None,
        compile_templates=False,
        keep_templates=False,
        statistics_fields_only=False,
    ):
        fake_fields = [
            klass(formdef=self.formdef)
            for klass in (
                filter_fields.InternalIdFilterField,
                filter_fields.NumberFilterField,
                filter_fields.IdentifierFilterField,
                filter_fields.PeriodStartFilterField,
                filter_fields.PeriodEndFilterField,
                filter_fields.PeriodStartUpdateTimeFilterField,
                filter_fields.PeriodEndUpdateTimeFilterField,
                filter_fields.UserIdFilterField,
                filter_fields.UserFunctionFilterField,
                filter_fields.SubmissionAgentFilterField,
                filter_fields.DistanceFilterField,
                filter_fields.CriticalityLevelFilterFiled,
            )
        ]
        criterias = []

        request = get_request()

        filters_dict = {}
        if self.view:
            filters_dict.update(self.view.get_filters_dict() or {})
            # ignore unconfigured criterias
            for k, v in list(filters_dict.items()):
                if k.endswith('-value') or k.endswith('-operator'):
                    continue
                if (f'{k}-value' not in filters_dict or not filters_dict[f'{k}-value']) and filters_dict.get(
                    f'{k}-operator', 'eq'
                ) == 'eq':
                    filters_dict.pop(k, None)
                    filters_dict.pop(f'{k}-value', None)
                    filters_dict.pop(f'{k}-operator', None)
        filters_dict.update(query_overrides or {})

        if request and request.form:
            request_form = request.form
        else:
            request_form = {}

        fake_fields_ids = [f.id for f in fake_fields]
        filters_in_request = {
            k.replace('filter-', '')
            for k in filters_dict
            if k.startswith('filter-') and not k.endswith('-value') and not k.endswith('-operator')
        }
        filters_in_request = {
            f
            for f in filters_in_request
            if f not in fake_fields_ids + ['status', 'user-uuid', 'submission-agent-uuid']
        }
        known_filters = set()

        values_in_request = {x for x in filters_dict if x.endswith('-value')}
        operators_in_request = {x for x in filters_dict if x.endswith('-operator') and x != 'filter-operator'}

        def report_error(error_message):
            if self.view_type == 'json':
                raise RequestError(error_message)
            if self.view_type == 'table':
                get_session().add_message(error_message, level='warning')
            criterias.append(Nothing())

        for filter_field in fake_fields + list(self.get_formdef_fields(include_block_items_fields=True)):
            values_in_request.discard(f'filter-{filter_field.contextual_id}-value')
            values_in_request.discard(f'filter-{filter_field.contextual_varname}-value')
            values_in_request.discard(f'multi-filter-{filter_field.contextual_id}-value')
            values_in_request.discard(f'multi-filter-{filter_field.contextual_varname}-value')
            operators_in_request.discard(f'filter-{filter_field.contextual_id}-operator')
            operators_in_request.discard(f'filter-{filter_field.contextual_varname}-operator')
            if not filter_field.available_for_filter:
                continue

            if statistics_fields_only and not getattr(filter_field, 'include_in_statistics', False):
                continue

            filter_field_key = None

            if filter_field.contextual_varname:
                # if this is a field with a varname and filter-%(varname)s is
                # present in the query string, enable this filter.
                if f'filter-{filter_field.contextual_varname}' in filters_dict:
                    filter_field_key = 'filter-%s' % filter_field.contextual_varname

            if filter_field.key == 'internal-id' and filters_dict.get('filter-internal-id'):
                # varname is 'internal_id' and not 'internal-id', fill filter-internal-id-value
                if filters_dict['filter-internal-id'] != 'on':
                    filters_dict['filter-internal-id-value'] = filters_dict['filter-internal-id']
                    if (
                        isinstance(filters_dict['filter-internal-id-value'], str)
                        and ',' in filters_dict['filter-internal-id-value']
                    ):
                        filters_dict['filter-internal-id-value'] = filters_dict[
                            'filter-internal-id-value'
                        ].split(',')

            if filter_field.key == 'number' and filters_dict.get('filter-number'):
                filters_dict['filter-number-value'] = filters_dict['filter-number']

            if filter_field.key == 'identifier' and filters_dict.get('filter-identifier'):
                filters_dict['filter-identifier-value'] = filters_dict['filter-identifier']

            if filter_field.key == 'distance' and filters_dict.get('filter-distance'):
                filters_dict['filter-distance-value'] = filters_dict['filter-distance']

            if filter_field.key == 'criticality-level' and filters_dict.get('filter-criticality-level'):
                if filters_dict['filter-criticality-level'] != 'on':
                    filters_dict['filter-criticality-level-value'] = filters_dict['filter-criticality-level']

            if filter_field.key == 'user-id' and not filters_dict.get('filter-user-function'):
                # convert uuid based filter into local id filter.
                # do not apply if there's filter-user-function as it indicates the filtering
                # should happen on function, not ownership.
                name_id = filters_dict.get('filter-user-uuid')
                if name_id:
                    nameid_users = get_publisher().user_class.get_users_with_name_identifier(name_id)
                    request_form['filter-user'] = filters_dict['filter-user'] = 'on'
                    if nameid_users:
                        filters_dict['filter-user-value'] = str(nameid_users[0].id)
                        request_form['filter-user-value'] = filters_dict['filter-user-value']
                    else:
                        filters_dict['filter-user-value'] = (
                            '__current__' if name_id == '__current__' else '-1'
                        )
                        request_form['filter-user-value'] = (
                            '__current__' if name_id == '__current__' else '-1'
                        )

            if filter_field.key == 'user-function' and filters_dict.get('filter-user-function'):
                if filters_dict.get('filter-user-function') != 'on':
                    # allow for short form, with a single query parameter
                    filters_dict['filter-user-function-value'] = filters_dict.get('filter-user-function')

            if filter_field.key == 'submission-agent':
                # convert uuid based filter into local id filter
                name_id = filters_dict.get('filter-submission-agent-uuid')
                if name_id:
                    nameid_users = get_publisher().user_class.get_users_with_name_identifier(name_id)
                    request_form['filter-submission-agent'] = filters_dict['filter-submission-agent'] = 'on'
                    if nameid_users:
                        filters_dict['filter-submission-agent-value'] = str(nameid_users[0].id)
                        request_form['filter-submission-agent-value'] = filters_dict[
                            'filter-submission-agent-value'
                        ]
                    else:
                        filters_dict['filter-submission-agent-value'] = '-1'
                        request_form['filter-submission-agent-value'] = '-1'

            if filter_field.key == 'user-function':
                # .../list?filter-user-function=on&filter-user-function-value=_manager&filter-user-uuid=c3...
                # .../list?filter-user-function=_manager&filter-user-uuid=c3...
                name_id = filters_dict.get('filter-user-uuid')
                if name_id and filters_dict.get('filter-user-function-value'):
                    nameid_users = get_publisher().user_class.get_users_with_name_identifier(name_id)
                    if nameid_users:
                        filters_dict['filter-user-function-value'] += ':%s' % nameid_users[0].id
                    else:
                        # no user with this uuid, change to filter on nobody
                        filters_dict['filter-user-function-value'] += ':__none__'

            if filters_dict.get('filter-%s' % filter_field.contextual_id):
                # if there's a filter-%(id)s, it is used to enable the actual
                # filter, and the value will be found in filter-%s-value.
                filter_field_key = 'filter-%s-value' % filter_field.contextual_id
                known_filters.add(filter_field.contextual_id)
            else:
                known_filters.add(filter_field.contextual_varname)

            if not filter_field_key:
                # if there's not known filter key, skip.
                continue

            filter_field_value = filters_dict.get(filter_field_key)
            if not filter_field_value and not (get_request() and get_request().is_api_url()):
                # ignore empty filters in UI
                continue

            # get operator and criteria
            filter_field_operator_key = '%s-operator' % filter_field_key.replace('-value', '')
            filter_field_operator = filters_dict.get(filter_field_operator_key) or 'eq'
            report_error_type = 'nothing'
            if not compile_templates:
                report_error_type = 'request-error' if self.view_type == 'json' else 'session-error'
            lazy_manager = LazyFormDefObjectsManager(
                formdef=self.formdef, report_error_type=report_error_type
            )

            # check value types
            if filter_field.key == 'internal-id':
                if filter_field_operator not in ['eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in']:
                    raise RequestError(
                        _('Invalid operator "%s" for "filter-internal-id".') % (filter_field_operator)
                    )

                def _report_error(value, operator):
                    if custom_view:
                        get_publisher().record_error(
                            _(
                                'Invalid value "%(value)s" for custom view "%(view)s", CardDef "%(card)s", '
                                'field "internal-id", operator "%(operator)s".'
                            )
                            % {
                                'value': value,
                                'view': custom_view.slug,
                                'card': custom_view.formdef.name,
                                'operator': operator,
                            }
                        )
                    else:
                        report_error(
                            _(
                                'Invalid value "%(value)s" for "filter-internal-id" and operator "%(operator)s".'
                            )
                            % {'value': value, 'operator': operator}
                        )

                if Template.is_template_string(filter_field_value, ezt_support=False):
                    if keep_templates:
                        # use Equal criteria here, the only use is in CardDef.get_data_source_referenced_varnames
                        criterias.append(Equal('id', filter_field_value))
                        continue
                    if not compile_templates:
                        criterias.append(Nothing())
                        continue
                    with get_publisher().complex_data():
                        value = WorkflowStatusItem.compute(filter_field_value, allow_complex=True)
                        filter_field_value = get_publisher().get_cached_complex_data(value)
                    if filter_field_value is None:
                        criterias.append(Nothing())
                        continue

                if filter_field_operator in ('in', 'not_in') and isinstance(filter_field_value, str):
                    filter_field_value = re.split(r'[\|,]', filter_field_value)

                if isinstance(filter_field_value, list):
                    try:
                        [int(v) for v in filter_field_value]
                    except (ValueError, TypeError):
                        _report_error(filter_field_value, filter_field_operator)
                        criterias.append(Nothing())
                        continue
                    if filter_field_operator in ('eq', 'in'):
                        criterias.append(Contains('id', filter_field_value))
                    elif filter_field_operator in ('ne', 'not_in'):
                        criterias.append(NotContains('id', filter_field_value))
                    else:
                        _report_error(filter_field_value, filter_field_operator)
                        criterias.append(Nothing())
                    continue
                try:
                    filter_field_value = int(filter_field_value)
                except (TypeError, ValueError):
                    report_error(
                        _('Invalid value "%(value)s" for "%(key)s".')
                        % {'value': filter_field_value, 'key': filter_field_key}
                    )
                    continue
            elif filter_field.key == 'period-date':
                try:
                    filter_date_value = misc.get_as_datetime(filter_field_value).timetuple()
                except ValueError:
                    continue
            elif filter_field_value == 'on' and filter_fields.is_unary_operator(filter_field_operator):
                filter_field_value = Ellipsis
            elif filter_field.key == 'bool':
                if filter_field_value == 'true':
                    filter_field_value = True
                elif filter_field_value == 'false':
                    filter_field_value = False
                else:
                    raise RequestError(
                        _('Invalid value "%(value)s" for "%(key)s".')
                        % {'value': filter_field_value, 'key': filter_field_key}
                    )
            elif filter_field.key in ('item', 'items', 'string', 'email', 'numeric', 'date'):
                if Template.is_template_string(filter_field_value, ezt_support=False):
                    if keep_templates:
                        # use Equal criteria here, the only use is in CardDef.get_data_source_referenced_varnames
                        criterias.append(Equal(filter_field.id, filter_field_value))
                        continue
                    if not compile_templates:
                        criterias.append(Nothing())
                        continue
                    filter_field_value = WorkflowStatusItem.compute(filter_field_value)

            if filter_field.key in ('item', 'items', 'bool', 'string', 'text', 'email', 'date', 'numeric'):
                operators = lazy_manager.get_field_allowed_operators(filter_field) or []
                if filter_field_operator not in [o[0] for o in operators]:
                    raise RequestError(
                        _('Invalid operator "%(operator)s" for "%(key)s".')
                        % {'operator': filter_field_operator, 'key': filter_field_key}
                    )
                if not filter_fields.is_unary_operator(filter_field_operator):
                    try:
                        filter_field_value = lazy_manager.format_value(
                            op=filter_field_operator,
                            value=filter_field_value,
                            field=filter_field,
                        )
                    except ValueError:
                        criterias.append(Nothing())
                        continue

            # add criteria
            if filter_field.key == 'internal-id':
                criterias.append(
                    lazy_manager.get_criteria_from_operator(
                        op=filter_field_operator, value=filter_field_value, field_id='id'
                    )
                )
            elif filter_field.key == 'number':
                criterias.append(Equal('id_display', str(filter_field_value)))
            elif filter_field.key == 'identifier':
                if filter_field_operator not in ['eq', 'ne']:
                    report_error(_('Invalid operator "%s" for "filter-identifier".') % filter_field_operator)
                    continue
                criterias.append(
                    self.formdef.get_by_multiple_id_criteria(
                        str(filter_field_value).split(','), operator=filter_field_operator
                    )
                )
            elif filter_field.key == 'period-date':
                if filter_field.id == 'start':
                    criterias.append(GreaterOrEqual('receipt_time', filter_date_value))
                elif filter_field.id == 'end':
                    criterias.append(LessOrEqual('receipt_time', filter_date_value))
                elif filter_field.id == 'start-mtime':
                    criterias.append(GreaterOrEqual('last_update_time', filter_date_value))
                elif filter_field.id == 'end-mtime':
                    criterias.append(LessOrEqual('last_update_time', filter_date_value))
            elif filter_field.key in ('submission-agent', 'user-id'):
                if filter_field_value == '__current__':
                    context_vars = get_publisher().substitutions.get_context_variables(mode='lazy')
                    if request and request.is_in_backoffice() and context_vars.get('form'):
                        # in case of backoffice submission/edition, take user associated
                        # with the form being submitted/edited, if any.
                        form_user = context_vars.get('form_user')
                        if form_user:
                            filter_field_value = str(form_user.id)
                    elif request and isinstance(request.user, get_publisher().user_class):
                        filter_field_value = str(request.user.id)
                    else:
                        filter_field_value = None
                if filter_field_value in ('__current__', None):
                    criterias.append(Nothing())
                elif filter_field.key == 'user-id':
                    criterias.append(Equal('user_id', filter_field_value))
                elif filter_field.key == 'submission-agent':
                    criterias.append(Equal('submission_agent_id', filter_field_value))
            elif filter_field.key == 'user-function':
                user_object = None
                context_vars = get_publisher().substitutions.get_context_variables(mode='lazy')
                if filter_field_value and ':' in filter_field_value:
                    filter_field_value, user_id = filter_field_value.split(':', 1)
                    user_object = None if user_id == '__none__' else get_publisher().user_class().get(user_id)
                elif request and request.is_in_backoffice() and context_vars.get('form'):
                    context_vars = get_publisher().substitutions.get_context_variables(mode='lazy')
                    user_object = unlazy(context_vars.get('form_user'))
                elif request:
                    user_object = request.user
                criterias.append(
                    ElementIntersects(
                        'workflow_merged_roles_dict',
                        filter_field_value,
                        user_object.get_roles() if user_object else None,
                    )
                )
            elif filter_field.key == 'distance':
                center_lat = request.form.get('center_lat') if request else None
                center_lon = request.form.get('center_lon') if request else None
                if not (center_lat and center_lon):
                    raise RequestError(_('Distance filter missing a center.'))
                center = misc.normalize_geolocation({'lat': center_lat, 'lon': center_lon})
                criterias.append(Distance(center, float(filter_field_value)))
            elif filter_field.key == 'criticality-level':
                try:
                    level = 100 + int(filter_field_value)
                except (TypeError, ValueError):
                    raise RequestError(
                        _('Invalid value "%(value)s" for "%(key)s".')
                        % {'value': filter_field_value, 'key': filter_field_key}
                    )
                criterias.append(Equal('criticality_level', level))
            elif filter_field.key in ('item', 'items', 'bool', 'string', 'text', 'email', 'date', 'numeric'):
                criterias.append(
                    lazy_manager.get_criteria_from_operator(
                        op=filter_field_operator,
                        value=filter_field_value,
                        field_id='f%s' % filter_field.id,
                        field=filter_field,
                    )
                )

        unknown_filters = sorted(filters_in_request - known_filters)
        if unknown_filters:
            error_message = ngettext(
                'Invalid filter "%(filters)s".',
                'Invalid filters "%(filters)s".',
                len(unknown_filters),
            ) % {'filters': _('", "').join(f for f in unknown_filters)}
            report_error(error_message)
            if custom_view is not None:
                for unknown_filter in unknown_filters:
                    get_publisher().record_error(
                        _('Invalid filter "%s".') % unknown_filter, formdef=self.formdef
                    )
        elif values_in_request or operators_in_request:
            error_message = ngettext(
                'Unused parameter "%(param)s".',
                'Unused parameters "%(param)s".',
                len(values_in_request | operators_in_request),
            ) % {'param': _('", "').join(f for f in sorted(values_in_request | operators_in_request))}
            report_error(error_message)

        return criterias

    def listing_top_actions(self):
        return ''

    @classmethod
    def get_multi_actions(cls, formdef, user):
        global_actions = formdef.workflow.get_global_manual_mass_actions()

        # include manual jumps with identifiers
        global_actions.extend(formdef.workflow.get_status_manual_mass_actions())

        mass_actions = []
        for action_dict in global_actions:
            # filter actions to get those that can be run by the user,
            # either because of actual roles, or because the action is
            # accessible to functions.
            if logged_users_role().id not in (action_dict.get('roles') or []):
                action_dict['roles'] = [x for x in user.get_roles() if x in (action_dict.get('roles') or [])]
            if action_dict['roles']:
                # action is accessible with user roles, remove mentions of functions
                action_dict['functions'] = []
            if action_dict['functions'] or action_dict['roles']:
                mass_actions.append(action_dict)

        return mass_actions

    def _q_index(self):
        self.view_type = 'table'
        self.check_access()

        self.use_default_view = True
        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        criterias = self.get_criterias_from_query()

        default_limit = int(get_publisher().get_site_option('default-page-size') or 20)
        limit = misc.get_int_or_400(get_request().form.get('limit', default_limit))
        # make sure limit is not too high as in pagination_links
        limit = min(limit, max(100, default_limit))
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        order_by = self.get_order_by_from_query()

        query = get_request().form.get('q')
        qs = ''
        if get_request().get_query():
            qs = '?' + get_request().get_query()

        multi_actions = self.get_multi_actions(self.formdef, get_request().user)
        form_attrs = {'data-has-view-settings': 'true'}
        if get_publisher().has_site_option('use-legacy-query-string-in-listings'):
            form_attrs = {}

        multi_form = Form(id='multi-actions', **form_attrs)
        for action in multi_actions:
            attrs = {}
            if action.get('functions'):
                for function in action.get('functions'):
                    # dashes are replaced by underscores to prevent HTML5
                    # normalization to CamelCase.
                    attrs['data-visible_for_%s' % function.replace('-', '_')] = 'true'
            else:
                attrs['data-visible_for_all'] = 'true'
            if action.get('statuses'):
                for status in action.get('statuses'):
                    attrs['data-visible_status_%s' % status] = 'true'
            else:
                attrs['data-visible_all_status'] = 'true'
            if getattr(action['action'], 'require_confirmation', False):
                attrs['data-ask-for-confirmation'] = (
                    getattr(action['action'], 'confirmation_text', None) or 'true'
                )
            multi_form.add_submit(
                'button-action-%s' % action['action'].id, action['action'].name, attrs=attrs
            )
        if not get_query_flag('ajax'):
            if multi_form.is_submitted() and get_request().form.get('select[]'):
                for action in multi_actions:
                    if multi_form.get_submit() == 'button-action-%s' % action['action'].id:
                        return self.submit_multi(
                            action,
                            selected_filter=selected_filter,
                            selected_filter_operator=selected_filter_operator,
                            query=query,
                            criterias=criterias,
                        )

        table = FormDefUI(self.formdef).listing(
            fields=fields,
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            limit=limit,
            offset=offset,
            query=query,
            order_by=order_by,
            criterias=criterias,
            include_checkboxes=bool(multi_actions),
        )

        if get_response().status_code == 302:
            # catch early redirect
            return table

        multi_form.widgets.append(HtmlWidget(table))
        if not multi_actions:
            multi_form.widgets.append(HtmlWidget('<div class="buttons"></div>'))

        audit('listing', obj=self.formdef, refresh=get_query_flag('ajax'))

        if get_query_flag('ajax'):
            get_request().ignore_session = True
            get_response().raw = True
            r = TemplateIO(html=True)
            r += multi_form.render()
            r += get_session().display_message()
            return r.getvalue()

        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        if self.view:
            view_name = self.view.title
            get_response().set_title(f'{self.view.title} - {self.formdef.name}')
            r += htmltext('<h2>%s - %s</h2>') % (self.formdef.name, view_name)
        else:
            get_response().set_title(self.formdef.name)
            r += htmltext('<h2>%s</h2>') % self.formdef.name
        r += self.listing_top_actions()
        r += htmltext('</div>')
        r += get_session().display_message()
        r += multi_form.render()

        get_response().filter['sidebar'] = self.get_formdata_sidebar(qs) + self.get_fields_sidebar(
            selected_filter,
            selected_filter_operator,
            fields,
            limit=limit,
            query=query,
            criterias=criterias,
            offset=offset,
            order_by=order_by,
        )

        return r.getvalue()

    def view_settings(self):
        form = copy.copy(get_request().form)
        action = form.pop('action', '.')
        if action not in ('.', 'map'):
            raise RequestError()
        for param in ('ajax', 'limit', 'offset', 'q', 'order_by'):
            form.pop(param, None)

        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size') or 20)
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        is_default_order_by, order_by = self.get_detailed_order_by_from_query()
        query = get_request().form.get('q', '')

        token = get_session().create_token(usage='view-settings', context=form)
        new_qs = {'st': token.id, 'limit': limit, 'offset': offset, 'q': query}
        if not is_default_order_by:
            new_qs['order_by'] = order_by
        encoded_new_qs = urllib.parse.urlencode(new_qs)
        uri = urllib.parse.urljoin(get_request().get_path(), action + '?' + encoded_new_qs)
        if get_query_flag('ajax'):
            get_response().set_content_type('application/json')
            method = getattr(self, '_q_index' if action == '.' else action)
            get_request().form = new_qs
            get_request().form['ajax'] = 'on'
            return json.dumps({'uri': uri, 'qs': encoded_new_qs, 'content': str(method())})
        return redirect(uri)

    def submit_multi(self, action, selected_filter, selected_filter_operator, query, criterias):
        item_ids = get_request().form['select[]']
        if '_all' in item_ids:
            criterias.append(Null('anonymised'))
            item_ids = FormDefUI(self.formdef).get_listing_item_ids(
                selected_filter,
                selected_filter_operator,
                user=get_request().user,
                query=query,
                criterias=criterias,
                order_by='receipt_time',
            )
        else:
            item_ids = self.formdef.data_class().get_sorted_ids(
                'receipt_time', [Contains('id', [int(x) for x in item_ids])]
            )

        if action['action'].is_interactive():
            return redirect(
                action['action'].get_global_interactive_form_url(formdef=self.formdef, ids=item_ids)
            )

        job = get_publisher().add_after_job(
            MassActionAfterJob(
                label=_('Executing task "%s" on forms') % action['action'].name,
                formdef=self.formdef,
                user_id=get_request().user.id,
                status_filter=selected_filter,
                status_filter_operator=selected_filter_operator,
                query_string=get_request().get_query(),
                action_id=action['action'].id,
                item_ids=item_ids,
                return_url=get_request().get_path_query(),
            )
        )
        job.store()
        return redirect(job.get_processing_url())

    def export_spreadsheet(self):
        self.use_default_view = True
        self.check_access()
        if self.formdef.category and not self.formdef.category.has_permission('export', get_request().user):
            raise errors.AccessForbiddenError()
        form = Form()
        form.add_hidden('query_string', get_request().get_query())
        formats = [
            ('ods', _('OpenDocument (.ods)'), 'ods'),
            ('csv', _('Text (.csv)'), 'csv'),
        ]
        if self.has_json_export_support:
            formats.append(('json', _('JSON'), 'json'))

        form.add(
            RadiobuttonsWidget,
            'format',
            options=formats,
            value='ods',
            required=True,
            extra_css_class='widget-inline-radio',
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            CheckboxWidget,
            'include_header_line',
            title=_('Include header line'),
            value=True,
            attrs={
                'data-dynamic-display-child-of': 'format',
                'data-dynamic-display-value-in': 'csv|ods',
            },
        )
        form.add_submit('submit', _('Export'))
        form.add_submit('cancel', _('Cancel'))

        if not form.is_submitted() or form.has_errors():
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Export Options')
            r += form.render()
            return r.getvalue()

        get_request().form = parse_query(form.get_widget('query_string').parse() or '', 'utf-8')
        get_request().form['skip_header_line'] = not (form.get_widget('include_header_line').parse())
        file_format = form.get_widget('format').parse()
        audit('export.%s' % file_format, obj=self.formdef)
        if file_format == 'csv':
            return self.csv()
        if file_format == 'json':
            return self.export_json_file()
        return self.ods()

    def csv(self):
        self.check_access()
        if (
            not get_request().is_api_url()
            and self.formdef.category
            and not self.formdef.category.has_permission('export', get_request().user)
        ):
            raise errors.AccessForbiddenError()
        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        user = get_request().user
        query = get_request().form.get('q')
        criterias = self.get_criterias_from_query()
        order_by = self.get_order_by_from_query(system_default_order_by='-id')
        skip_header_line = bool(get_request().form.get('skip_header_line'))

        count = self.formdef.data_class().count()
        job = CsvExportAfterJob(
            self.formdef,
            fields=fields,
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
            order_by=order_by,
            skip_header_line=skip_header_line,
        )
        if count > self.WCS_SYNC_EXPORT_LIMIT:
            job = get_publisher().add_after_job(job)
            job.store()
            return redirect(job.get_processing_url())

        job.id = job.DO_NOT_STORE
        job.execute()
        response = get_response()
        response.set_content_type('text/plain')
        response.set_header('content-disposition', 'attachment; filename=%s.csv' % self.formdef.url_name)
        return job.result_file.get_content()

    def export(self):
        self.check_access()
        try:
            job = AfterJob.get(get_request().form.get('download'))
        except KeyError:
            return redirect('.')

        if not job.status == 'completed':
            raise errors.TraversalError()
        response = get_response()
        response.set_content_type(job.content_type)
        response.set_header('content-disposition', 'attachment; filename=%s' % job.file_name)
        return job.result_file.get_content()

    def ods(self):
        self.check_access()
        if get_request().has_anonymised_data_api_restriction():
            # api/ will let this pass but we don't want that.
            raise errors.AccessForbiddenError()
        if (
            not get_request().is_api_url()
            and self.formdef.category
            and not self.formdef.category.has_permission('export', get_request().user)
        ):
            raise errors.AccessForbiddenError()
        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        user = get_user_from_api_query_string() or get_request().user
        query = get_request().form.get('q')
        criterias = self.get_criterias_from_query()
        order_by = self.get_order_by_from_query(system_default_order_by='-id')
        skip_header_line = bool(get_request().form.get('skip_header_line'))

        count = self.formdef.data_class().count()
        job = OdsExportAfterJob(
            self.formdef,
            fields=fields,
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
            order_by=order_by,
            skip_header_line=skip_header_line,
        )
        if count > self.WCS_SYNC_EXPORT_LIMIT and not get_request().is_api_url():
            job = get_publisher().add_after_job(job)
            job.store()
            return redirect(job.get_processing_url())

        job.id = job.DO_NOT_STORE
        job.execute()
        response = get_response()
        response.set_content_type('application/vnd.oasis.opendocument.spreadsheet')
        response.set_header('content-disposition', 'attachment; filename=%s.ods' % self.formdef.url_name)
        return job.result_file.get_content()

    def export_json_file(self):
        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        user = get_request().user
        query = get_request().form.get('q')
        criterias = self.get_criterias_from_query()
        order_by = self.get_order_by_from_query()

        job = JsonFileExportAfterJob(
            self.formdef,
            fields=fields,
            selected_filter=selected_filter,
            selected_filter_operator=selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
            order_by=order_by,
        )
        job = get_publisher().add_after_job(job)
        job.store()
        return redirect(job.get_processing_url())

    def json(self):
        self.view_type = 'json'
        anonymise = get_request().has_anonymised_data_api_restriction()
        self.check_access(api_name='list')
        get_response().set_content_type('application/json')
        user = get_user_from_api_query_string() or get_request().user if not anonymise else None
        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query(default='all')
        selected_filter_operator = self.get_filter_operator_from_query()
        criterias = self.get_criterias_from_query()
        order_by = self.get_order_by_from_query()
        query = get_request().form.get('q') if not anonymise else None
        offset = None
        if 'offset' in get_request().form:
            offset = misc.get_int_or_400(get_request().form['offset'])
        limit = None
        if 'limit' in get_request().form:
            limit = misc.get_int_or_400(get_request().form['limit'])

        if not get_query_flag('include-anonymised', default=False):
            criterias.append(Null('anonymised'))

        common_statuses = [id for id, name, _ in get_common_statuses()]

        if selected_filter not in common_statuses:
            # filtering by status can be done with status ID or name,
            # we handle the mapping here
            for status in self.formdef.workflow.possible_status or []:
                if selected_filter in [status.id, status.name]:
                    selected_filter = status.id
                    break

        items, total_count = FormDefUI(self.formdef).get_listing_items(
            fields,
            selected_filter,
            selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
            order_by=order_by,
            anonymise=anonymise,
            offset=offset,
            limit=limit,
        )
        digest_key = 'default'
        if self.view and isinstance(self.formdef, CardDef):
            view_digest_key = 'custom-view:%s' % self.view.get_url_slug()
            if view_digest_key in (self.formdef.digest_templates or {}):
                digest_key = view_digest_key
        full = get_query_flag('full')
        include_fields = get_query_flag('include-fields') or full
        include_user = get_query_flag('include-user') or include_fields or full
        include_evolution = get_query_flag('include-evolution') or full
        include_roles = get_query_flag('include-roles') or full
        include_submission = get_query_flag('include-submission') or full
        include_workflow = get_query_flag('include-workflow') or full
        include_workflow_data = get_query_flag('include-workflow-data') or full
        include_actions = get_query_flag('include-actions') or full
        # noqa pylint: disable=too-many-boolean-expressions
        if (
            include_fields
            or include_evolution
            or include_roles
            or include_submission
            or include_user
            or include_workflow
            or include_workflow_data
            or include_actions
        ):
            job = JsonFileExportAfterJob(self.formdef)
            job.id = job.DO_NOT_STORE
            output = list(
                job.create_json_export(
                    items,
                    user=user,
                    anonymise=anonymise,
                    digest_key=digest_key,
                    include_evolution=include_evolution,
                    include_files=False,
                    include_roles=include_roles,
                    include_submission=include_submission,
                    include_fields=include_fields,
                    include_user=include_user,
                    include_unnamed_fields=False,
                    include_workflow=include_workflow,
                    include_workflow_data=include_workflow_data,
                    include_actions=include_actions,
                    values_at=get_request().form.get('at'),
                    related_fields=[x for x in fields if x.key == 'related-field'],
                )
            )
        else:

            def get_formdata_json(filled):
                data = {
                    'id': filled.identifier,
                    'internal_id': str(filled.id),
                    'display_id': filled.get_display_id(),
                    'display_name': filled.get_display_name(),
                    'digest': (filled.digests or {}).get(digest_key),
                    'text': filled.get_display_label(digest_key=digest_key),
                    'url': filled.get_url(),
                    'receipt_time': (
                        make_naive(filled.receipt_time.replace(microsecond=0))
                        if filled.receipt_time
                        else None
                    ),
                    'last_update_time': (
                        make_naive(filled.last_update_time.replace(microsecond=0))
                        if filled.last_update_time
                        else None
                    ),
                }
                if hasattr(filled, 'uuid'):
                    data['uuid'] = filled.uuid
                return data

            output = [get_formdata_json(x) for x in items]

        default_response_type = 'list'
        if isinstance(self.formdef, CardDef) or self.view:
            # use dict response type by default for cards and when using
            # custom views as this is a better format and cards and views
            # were never returned as list.
            default_response_type = 'dict'

        response_type = get_request().form.get('response_type') or default_response_type

        if response_type == 'dict':
            # return results in dictionary when explicitely requested, or for cards and views
            # as they never returned results as a list.
            output = {
                'err': 0,
                'count': total_count,
                'data': output,
            }
            if limit:
                # add next / prev links to mimic DRF api
                request = get_publisher().get_request()
                current_url = request.get_url()
                qs = urllib.parse.parse_qs(request.get_query())
                offset = offset if offset else 0
                if offset > 0:
                    qs['offset'] = offset - limit if offset - limit > 0 else 0
                    prev_qs = urllib.parse.urlencode(qs, doseq=True)
                    output['previous'] = f'{current_url}?{prev_qs}'
                if offset + limit < total_count:
                    qs['offset'] = offset + limit
                    next_qs = urllib.parse.urlencode(qs, doseq=True)
                    output['next'] = f'{current_url}?{next_qs}'

        # legacy response format (straight list)
        return json.dumps(output, cls=misc.JSONEncoder)

    def geojson(self):
        if not self.formdef.geolocations:
            raise errors.TraversalError()
        if get_request().has_anonymised_data_api_restriction():
            # api/ will let this pass but we don't want that.
            raise errors.AccessForbiddenError()
        self.check_access('geojson')
        get_response().set_content_type('application/json')
        self.view_type = 'json'

        user = get_request().user
        if not user:
            user = get_user_from_api_query_string('geojson')

        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        if get_request().form.get('full') == 'on':
            fields = list(self.get_formdef_fields(include_block_fields=False))
        else:
            fields = self.get_fields_from_query()
        criterias = self.get_criterias_from_query()
        criterias.append(Null('anonymised'))
        query = get_request().form.get('q')

        items = FormDefUI(self.formdef).get_listing_items(
            fields,
            selected_filter,
            selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
        )[0]

        return json.dumps(geojson_formdatas(items, fields=fields), cls=misc.JSONEncoder)

    def ics(self):
        if get_request().has_anonymised_data_api_restriction():
            # api/ will let this pass but we don't want that.
            raise errors.AccessForbiddenError()

        if not get_request().user and get_request().form.get('api-user'):
            # custom query string authentification as some Outlook versions and
            # Google calendar do not (longer) support HTTP basic authentication.
            try:
                get_request()._user = ApiAccess.get_with_credentials(
                    get_request().form.get('api-user', ''), get_request().form.get('api-key', '')
                )
            except KeyError:
                self._user = None
        self.check_access('ics')
        user = get_request().user
        if not (user and user.is_admin):
            user = get_user_from_api_query_string('ics') or user

        formdef = self.formdef
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()
        fields = self.get_fields_from_query()
        criterias = self.get_criterias_from_query()
        query = get_request().form.get('q')

        class IcsDirectory(Directory):
            # ics/<component> with <component> being the identifier (varname)
            # of the field to use as start date (may be a date field or a
            # string field).
            # ics/<component>/<component2> with <component2> as the identifier
            # of the field to use as end date (ditto, date or string field)
            def _q_traverse(self, path):
                if not path[-1]:
                    # allow trailing slash
                    path = path[:-1]
                if len(path) not in (1, 2):
                    raise errors.TraversalError()
                start_date_field_varname = path[0]
                end_date_field_varname = None
                if len(path) == 2:
                    end_date_field_varname = path[1]

                start_date_field_id = None
                end_date_field_id = None
                start_date_field_type = None
                end_date_field_type = None

                if 'form_var_' not in start_date_field_varname:
                    for field in formdef.get_all_fields():
                        if getattr(field, 'varname', None) == start_date_field_varname:
                            start_date_field_id = field.id
                            start_date_field_type = field.key
                            break
                    else:
                        raise errors.TraversalError()

                if end_date_field_varname and 'form_var_' not in end_date_field_varname:
                    for field in formdef.get_all_fields():
                        if getattr(field, 'varname', None) == end_date_field_varname:
                            end_date_field_id = field.id
                            end_date_field_type = field.key
                            break
                    else:
                        raise errors.TraversalError()

                formdatas = FormDefUI(formdef).get_listing_items(
                    fields,
                    selected_filter,
                    selected_filter_operator,
                    user=user,
                    query=query,
                    criterias=criterias,
                )[0]

                cal = vobject.iCalendar()
                cal.add('prodid').value = '-//Entr\'ouvert//NON SGML Publik'

                def get_date_val(date_field_id, date_field_varname, date_field_type):
                    if date_field_id:
                        date_val = formdata.data.get(date_field_id)
                    else:
                        # get date using lazy formdata
                        date_val = formdata_lazy_vars.get(date_field_varname)
                        if hasattr(date_val, 'get_value'):
                            # get field type
                            date_field_type = date_val._field.key
                            date_val = date_val.get_value()

                    if date_val:
                        try:
                            dt = make_datetime(date_val)
                            if date_field_type == 'date':
                                dt = dt.date()
                        except ValueError:
                            dt = None
                    else:
                        dt = None
                    return dt

                for formdata in formdatas:
                    formdata_lazy_vars = CompatibilityNamesDict(
                        formdata.get_substitution_variables(minimal=True)
                    )

                    dtstart = get_date_val(
                        start_date_field_id, start_date_field_varname, start_date_field_type
                    )
                    if dtstart is None:
                        continue

                    dtend = None
                    if end_date_field_varname:
                        dtend = get_date_val(end_date_field_id, end_date_field_varname, end_date_field_type)

                    vevent = vobject.newFromBehavior('vevent')
                    vevent.add('uid').value = '%s-%s-%s' % (
                        get_request().get_server().lower(),
                        formdef.url_name,
                        formdata.id,
                    )
                    summary = formdata.get_display_name()
                    if formdata.default_digest:
                        summary += ' - %s' % formdata.default_digest
                    vevent.add('summary').value = summary
                    vevent.add('dtstart').value = dtstart
                    if isinstance(dtstart, datetime.datetime):
                        vevent.dtstart.value_param = 'DATE-TIME'
                    else:
                        vevent.dtstart.value_param = 'DATE'
                    if dtend:
                        vevent.add('dtend').value = dtend
                        if isinstance(dtend, datetime.datetime):
                            vevent.dtend.value_param = 'DATE-TIME'
                        else:
                            vevent.dtend.value_param = 'DATE'
                    backoffice_url = formdata.get_url(backoffice=True)
                    vevent.add('url').value = backoffice_url
                    form_name = formdef.name
                    status_name = formdata.get_status_label()
                    description = '%s | %s | %s\n' % (form_name, formdata.get_display_id(), status_name)
                    if formdata.default_digest:
                        description += '%s\n' % formdata.default_digest
                    description += backoffice_url
                    # TODO: improve performance by loading all users in one
                    # single query before the loop
                    if formdata.user:
                        description += '\n%s' % formdata.user.get_display_name()
                    vevent.add('description').value = description
                    cal.add(vevent)

                get_response().set_content_type('text/calendar')
                return cal.serialize()

        return IcsDirectory()

    def map(self):
        self.view_type = 'map'
        self.use_default_view = True
        get_response().add_javascript(['qommon.map.js'])
        get_response().set_title('%s - %s' % (_('Form'), self.formdef.name))
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('map', _('Map')))
        attrs = {
            'class': 'qommon-map',
            'id': 'backoffice-map',
            'data-readonly': True,
            'data-geojson-url': '%s/geojson?%s' % (get_request().get_url(1), get_request().get_query()),
        }
        attrs.update(get_publisher().get_map_attributes())

        fields = self.get_fields_from_query()
        selected_filter = self.get_filter_from_query()
        selected_filter_operator = self.get_filter_operator_from_query()

        qs = ''
        if get_request().get_query():
            qs = '?' + get_request().get_query()
        get_response().filter['sidebar'] = self.get_formdata_sidebar(qs) + self.get_fields_sidebar(
            selected_filter, selected_filter_operator, fields, action='map'
        )

        if not get_query_flag('ajax'):
            r += htmltext('<h2>%s - %s</h2>') % (self.formdef.name, _('Map'))
        r += htmltext('<div><div %s></div></div>' % ' '.join(['%s="%s"' % x for x in attrs.items()]))
        return r.getvalue()

    def get_stats_sidebar(self, selected_filter):
        get_response().add_javascript(['wcs.listing.js'])
        r = TemplateIO(html=True)
        r += htmltext('<form id="listing-settings" action="stats">')
        r += self.get_filter_sidebar(selected_filter=selected_filter, mode='stats')
        if '<select name="filter">' not in str(r.getvalue()):
            r += htmltext('<input type="hidden" name="filter" value="all">')
        r += htmltext('<button class="submit-button">%s</button>') % _('Submit')
        r += htmltext('</form>')
        return r.getvalue()

    def stats(self):
        self.check_access()
        if self.formdef.category and not self.formdef.category.has_permission(
            'statistics', get_request().user
        ):
            raise errors.AccessForbiddenError()
        get_response().set_title('%s - %s' % (_('Form'), self.formdef.name))
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('stats', _('Statistics')))

        selected_filter = self.get_filter_from_query(default='all')
        criterias = self.get_criterias_from_query()
        get_response().filter['sidebar'] = self.get_formdata_sidebar() + self.get_stats_sidebar(
            selected_filter
        )

        criterias.append(StrictNotEqual('status', 'draft'))
        criterias += FormDefUI(self.formdef).get_status_criterias(selected_filter, 'eq', get_request().user)

        values = self.formdef.data_class().select(criterias)
        # load all evolutions in a single batch, to avoid as many query as
        # there are formdata when computing resolution times statistics.
        self.formdef.data_class().load_all_evolutions(values)

        r += htmltext('<div class="warningnotice"><p>%s <a href="%s">%s</a></p></div>') % (
            _('This view is deprecated and will soon be removed.'),
            'https://doc-publik.entrouvert.com/agent-traitant/statistiques-internes/#statistiques-internes',
            _('More information in documentation.'),
        )
        r += htmltext('<div id="statistics">')
        r += htmltext('<div class="splitcontent-left">')

        no_forms = len(values)
        r += htmltext('<div class="bo-block">')
        r += htmltext('<p>%s %d</p>') % (_('Total number of records:'), no_forms)

        if self.formdef.workflow:
            r += htmltext('<ul>')
            for status in self.formdef.workflow.possible_status:
                r += htmltext('<li>%s: %d</li>') % (
                    status.name,
                    len([x for x in values if x.status == 'wf-%s' % status.id]),
                )
            r += htmltext('</ul>')
        r += htmltext('</div>')

        excluded_fields = []
        for criteria in criterias:
            if not isinstance(criteria, Equal):
                continue
            excluded_fields.append(criteria.attribute[1:])

        stats_for_fields = self.stats_fields(values, excluded_fields=excluded_fields)
        if stats_for_fields:
            r += htmltext('<div class="bo-block">')
            r += stats_for_fields
            r += htmltext('</div>')

        stats_times = self.stats_resolution_time(criterias)
        if stats_times:
            r += htmltext('<div class="bo-block">')
            r += stats_times
            r += htmltext('</div>')

        r += htmltext('</div>')
        r += htmltext('<div class="splitcontent-right">')
        criterias.append(Equal('formdef_id', int(self.formdef.id)))
        r += do_graphs_section(criterias=criterias)
        r += htmltext('</div>')

        r += htmltext('</div>')  # id="statistics"

        if get_query_flag('ajax'):
            get_request().ignore_session = True
            get_response().raw = True
            return r.getvalue()

        page = TemplateIO(html=True)
        page += htmltext('<h2>%s - %s</h2>') % (self.formdef.name, _('Statistics'))
        page += htmltext(r)
        page += htmltext('<a class="back" href=".">%s</a>') % _('Back')
        return page.getvalue()

    def stats_fields(self, values, excluded_fields=None):
        r = TemplateIO(html=True)
        had_page = False
        last_page = None
        last_title = None
        for f in self.formdef.get_all_fields():
            if excluded_fields and f.id in excluded_fields:
                continue
            if f.key == 'page':
                last_page = f.label
                last_title = None
                continue
            if f.key == 'title':
                last_title = f.label
                continue
            if not f.stats:
                continue
            t = f.stats(values)
            if not t:
                continue
            if last_page:
                if had_page:
                    r += htmltext('</div>')
                r += htmltext('<div class="page">')
                r += htmltext('<h3>%s</h3>') % last_page
                had_page = True
                last_page = None
            if last_title:
                r += htmltext('<h3>%s</h3>') % last_title
                last_title = None
            r += t

        if had_page:
            r += htmltext('</div>')

        return r.getvalue()

    @staticmethod
    def alter_resolution_times_criterias(criterias):
        def alter_criteria(criteria):
            # change attributes to point to the formdata table (f)
            if hasattr(criteria, 'attribute'):
                criteria.attribute = f'f.{criteria.attribute}'
            elif hasattr(criteria, 'criteria'):  # Not()
                alter_criteria(criteria.criteria)
            elif hasattr(criteria, 'criterias'):  # Or()
                for c in criteria.criterias:
                    alter_criteria(c)

        altered_criterias = []
        for criteria in criterias:
            altered_criteria = copy.deepcopy(criteria)
            alter_criteria(altered_criteria)
            altered_criterias.append(altered_criteria)

        return altered_criterias

    def stats_resolution_time(self, criterias):
        possible_status = [('wf-%s' % x.id, x.id) for x in self.formdef.workflow.possible_status[1:]]

        if len(possible_status) < 2:
            return

        start_status = 'wf-%s' % self.formdef.workflow.possible_status[0].id

        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Resolution time')

        for wf_status_id, status_id in possible_status:
            res_time_forms = self.formdef.data_class().get_resolution_times(
                start_status,
                [wf_status_id],
                criterias=self.alter_resolution_times_criterias(criterias),
                prefix_criterias=False,
            )
            res_time_forms = [x[0] for x in res_time_forms]
            if not res_time_forms:
                continue
            sum_times = sum(res_time_forms)
            len_times = len(res_time_forms)
            min_times = res_time_forms[0]
            max_times = res_time_forms[-1]
            r += htmltext('<h3>%s</h3>') % (
                _('To Status "%s"') % self.formdef.workflow.get_status(status_id).name
            )
            r += htmltext('<ul class="resolution-times status-%s">' % wf_status_id)
            r += htmltext(' <li>%s %s</li>') % (_('Count:'), len_times)
            r += htmltext(' <li>%s %s</li>') % (_('Minimum Time:'), format_time(min_times))
            r += htmltext(' <li>%s %s</li>') % (_('Maximum Time:'), format_time(max_times))
            r += htmltext(' <li>%s %s</li>') % (_('Range:'), format_time(max_times - min_times))
            mean = sum_times // len_times
            r += htmltext(' <li>%s %s</li>') % (_('Mean:'), format_time(mean))
            if len_times % 2:
                median = res_time_forms[len_times // 2]
            else:
                midpt = len_times // 2
                median = (res_time_forms[midpt - 1] + res_time_forms[midpt]) // 2
            r += htmltext(' <li>%s %s</li>') % (_('Median:'), format_time(median))

            # variance...
            x = 0
            for t in res_time_forms:
                x += (t - mean) ** 2.0
            try:
                variance = x // (len_times + 1)
            except Exception:
                variance = 0
            # not displayed since in square seconds which is not easy to grasp

            from math import sqrt

            # and standard deviation
            std_dev = sqrt(variance)
            r += htmltext(' <li>%s %s</li>') % (_('Standard Deviation:'), format_time(std_dev))

            r += htmltext('</ul>')

        return r.getvalue()

    def _q_lookup_view(self, component):
        if not self.view:
            view_slug = component
            criterias = []
            if view_slug.startswith('user-'):
                view_slug = view_slug[5:]
                criterias.append(Equal('visibility', 'owner'))
            else:
                criterias.append(NotEqual('visibility', 'owner'))
            criterias.append(Equal('slug', view_slug))
            for view in self.get_custom_views(criterias):
                return self.__class__(formdef=self.formdef, view=view)
            if component.startswith('user-'):
                get_session().add_message(
                    _(
                        'A missing or invalid custom view was referenced; '
                        'you have been automatically redirected.'
                    ),
                    level='warning',
                )
                # remove custom view reference from path
                # (ignore the fact that some form/card could itself be named
                # user-whatever)
                url = get_request().get_path_query().replace('/%s/' % component, '/')
                return misc.QLookupRedirect(url)
            if get_publisher().custom_view_class.exists(
                [
                    Equal('formdef_type', self.formdef.xml_root_node),
                    Equal('formdef_id', str(self.formdef.id)),
                    Equal('slug', view_slug),
                    Equal('visibility', 'role'),
                ]
            ):
                get_session().add_message(
                    _(
                        'A custom view for which you do not have access rights was referenced; '
                        'you have been automatically redirected to the default view.'
                    ),
                    level='warning',
                )
                # remove custom view reference from path
                path_parts = get_request().get_path_query().split('/')
                del path_parts[4]  # ['', 'backoffice', 'management or data', 'slug', 'view name', '...']
                return misc.QLookupRedirect('/'.join(path_parts))

    def _q_lookup(self, component):
        if component == 'ics':
            return self.ics()

        view_lookup_response = self._q_lookup_view(component)
        if view_lookup_response is not None:
            return view_lookup_response

        try:
            filled = self.formdef.data_class().get_by_id(component)
        except KeyError:
            raise errors.TraversalError()

        return FormBackOfficeStatusPage(self.formdef, filled, parent_view=self)

    def live(self):
        return FormBackofficeEditPage(self.formdef.url_name).live()


class FormBackofficeEditPage(FormFillPage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_mode = True

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        form.attrs['data-live-url'] = self.formdef.get_url(backoffice=True) + 'live'
        return form

    def is_missing_user(self):
        if self.edited_data.user_id:
            return False
        return super().is_missing_user()


class FormBackOfficeStatusPage(FormStatusPage):
    _q_exports_orig = [
        '',
        'download',
        'json',
        'action',
        'live',
        'inspect',
        'tempfile',
        'tsupdate',
        ('inspect-tool', 'inspect_tool'),
        ('download-as-zip', 'download_as_zip'),
        ('lateral-block', 'lateral_block'),
        ('user-pending-forms', 'user_pending_forms'),
        ('check-workflow-progress', 'check_workflow_progress'),
        'scan',
    ]
    form_page_class = FormBackofficeEditPage

    sidebar_recorded_message = _('The form has been recorded on %(date)s with the number %(identifier)s.')
    sidebar_recorded_by_agent_message = _(
        'The form has been recorded on %(date)s with the number %(identifier)s by %(agent)s.'
    )

    def _q_index(self):
        if self.filled.status == 'draft':
            if self.filled.backoffice_submission and self.formdef.backoffice_submission_roles:
                for role in get_request().user.get_roles():
                    if role in self.formdef.backoffice_submission_roles:
                        return redirect(
                            '../../../submission/%s/%s/' % (self.formdef.url_name, self.filled.id)
                        )
            raise errors.AccessForbiddenError()

        get_response().filter['sidebar'] = self.get_sidebar()
        return self.status()

    def receipt(self, *args, **kwargs):
        r = TemplateIO(html=True)
        if get_session() and get_session().is_anonymous_submitter(self.filled):
            r += htmltext('<div class="infonotice">')
            r += str(
                _(
                    'This form has been accessed via its tracking code, it is '
                    'therefore displayed like you were also its owner.'
                )
            )
            r += htmltext('</div>')
        r += super().receipt(*args, **kwargs)
        return r.getvalue()

    def get_sidebar(self):
        return self.get_extra_context_bar()

    def get_workflow_form(self, user):
        form = super().get_workflow_form(user)
        if form:
            form.attrs['data-live-url'] = self.filled.get_url(backoffice=True) + 'live'
        return form

    def lateral_block(self):
        self.check_receiver()
        get_response().raw = True
        response = self.get_lateral_block()
        return response

    def user_pending_forms(self):
        self.check_receiver()
        get_response().raw = True
        response = self.get_user_pending_forms()

        # preemptive locking of forms
        all_visitors = get_session().get_object_visitors(self.filled)
        visitors = [x for x in all_visitors if x[0] != get_session().user]
        me_in_visitors = bool(get_session().user in [x[0] for x in all_visitors])

        if not visitors or me_in_visitors:
            related_user_forms = getattr(self.filled, 'related_user_forms', None) or []
            user_roles = set(get_request().user.get_roles())
            session = get_session()
            get_publisher().substitutions.unfeed(lambda x: x is self.filled)
            for user_formdata in related_user_forms:
                if user_roles.intersection(
                    user_formdata.get_actions_roles(condition_kwargs={'record_errors': False})
                ):
                    session.mark_visited_object(user_formdata)

        return response

    def can_go_in_inspector(self):
        if get_publisher().get_backoffice_root().is_global_accessible('worflows'):
            return True
        if (
            get_publisher()
            .get_backoffice_root()
            .is_global_accessible(self.formdata.formdef.backoffice_section)
        ):
            return True

        user_roles = set(get_request().user.get_roles())
        for category in (self.formdata.formdef.category, self.formdata.formdef.workflow.category):
            if not category:
                continue
            management_roles = {x.id for x in getattr(category, 'management_roles') or []}
            if user_roles.intersection(management_roles):
                return True
        return False

    def get_extra_context_bar(self, parent=None):
        formdata = self.filled
        management_sidebar_items = self.formdef.get_management_sidebar_items()

        r = TemplateIO(html=True)

        if not formdata.is_draft():
            if get_request().form.get('origin') == 'global':
                url = '/backoffice/management/listing'
            else:
                url = '..'
            r += htmltext('<p><a class="button" id="back-to-listing" href="%s">%s</a></p>') % (
                url,
                _('Back to Listing'),
            )
            if (
                formdata.backoffice_submission
                and formdata.submission_agent_id == str(get_request().user.id)
                and formdata.tracking_code
                and (now() - formdata.receipt_time) < datetime.timedelta(minutes=30)
            ):
                # keep displaying tracking code to submission agent for 30
                # minutes after submission
                r += htmltext('<div class="extra-context">')
                r += htmltext('<h3>%s</h3>') % _('Tracking Code')
                r += htmltext('<p>%s</p>') % formdata.tracking_code
                r += htmltext('</div>')

        if not formdata.is_draft() and 'general' in management_sidebar_items:
            r += htmltext('<div class="extra-context sidebar-general-information">')
            r += htmltext('<h3>%s</h3>') % _('General Information')
            r += htmltext('<p>')
            tm = misc.localstrftime(formdata.receipt_time)
            agent_user = None
            if formdata.submission_agent_id:
                agent_user = get_publisher().user_class.get(formdata.submission_agent_id, ignore_errors=True)

            if agent_user:
                r += self.sidebar_recorded_by_agent_message % {
                    'date': tm,
                    'identifier': formdata.get_display_id(),
                    'agent': agent_user.get_display_name(),
                }
            else:
                r += self.sidebar_recorded_message % {'date': tm, 'identifier': formdata.get_display_id()}
            r += htmltext('</p>')
            try:
                status_colour = formdata.get_status().colour
            except AttributeError:
                status_colour = None
            status_colour = status_colour or '#ffffff'
            fg_colour = misc.get_foreground_colour(status_colour)

            r += htmltext(
                '<p class="current-status"><span class="item" style="background: %s; color: %s;"></span>'
                % (status_colour, fg_colour)
            )
            r += htmltext('<span>%s %s') % (_('Status:'), formdata.get_status_label())
            status = formdata.get_status()
            if status:
                visibility_mode_str = status.get_visibility_mode_str()
                if visibility_mode_str:
                    r += htmltext('<span class="visibility-off" title="%s"></span>') % visibility_mode_str
            r += htmltext('</span></p>')
            if formdata.formdef.workflow.criticality_levels:
                try:
                    level = formdata.get_criticality_level_object()
                except IndexError:
                    pass
                else:
                    r += htmltext('<p class="current-level">')
                    if level.colour:
                        r += htmltext('<span class="item" style="background: %s;"></span>' % level.colour)
                    r += htmltext('<span>%s %s</span></p>') % (_('Criticality Level:'), level.name)

            if formdata.anonymised:
                r += htmltext('<div class="infonotice">')
                r += htmltext(_('This form has been anonymised on %(date)s.')) % {
                    'date': formdata.anonymised.strftime(misc.date_format())
                }
                r += htmltext('</div>')
            r += htmltext('</div>')  # .extra-context

        if not formdata.is_draft() and 'download-files' in management_sidebar_items:
            has_attached_files = False
            for value in (formdata.data or {}).values():
                if isinstance(value, PicklableUpload):
                    has_attached_files = True
                if isinstance(value, dict) and isinstance(value.get('data'), list):
                    # block fields
                    for subvalue in value.get('data'):
                        for subvalue_elem in subvalue.values():
                            if isinstance(subvalue_elem, PicklableUpload):
                                has_attached_files = True
                                break
                if has_attached_files:
                    break

            if has_attached_files:
                r += htmltext('<div class="extra-context sidebar-download-files">')
                r += htmltext('<p><a class="button" href="download-as-zip">%s</a></p>') % _(
                    'Download all files as .zip'
                )
                r += htmltext('</div>')

        if 'submission-context' in management_sidebar_items:
            r += self.get_extra_submission_context_bar()
            r += self.get_extra_submission_channel_bar()

        if 'user' in management_sidebar_items:
            r += self.get_extra_submission_user_id_bar(parent=parent)

        if 'geolocation' in management_sidebar_items:
            r += self.get_extra_geolocation_bar()

        if 'custom-template' in management_sidebar_items and formdata.formdef.lateral_template:
            r += htmltext('<div data-async-url="%slateral-block"></div>' % formdata.get_url(backoffice=True))

        if (
            'pending-forms' in management_sidebar_items
            and not isinstance(formdata.formdef, CardDef)
            and formdata.user_id
        ):
            r += htmltext(
                '<div data-async-url="%suser-pending-forms"></div>' % formdata.get_url(backoffice=True)
            )

        if not formdata.is_draft() and self.can_go_in_inspector():
            r += htmltext('<div class="extra-context sidebar-data-inspector">')
            r += htmltext('<p><a href="%sinspect">' % formdata.get_url(backoffice=True))
            r += htmltext('%s</a></p>') % _('Data Inspector')
            r += htmltext('</div>')

        return r.getvalue()

    def get_extra_submission_context_bar(self):
        formdata = self.filled
        r = TemplateIO(html=True)
        if formdata.submission_context or formdata.submission_channel:
            extra_context = formdata.submission_context or {}
            r += htmltext('<div class="extra-context sidebar-submission-context">')
            if extra_context.get('orig_formdef_id'):
                object_type = extra_context.get('orig_object_type', 'formdef')
                if object_type == 'formdef':
                    r += htmltext('<h3>%s</h3>') % _('Original form')
                    object_class = FormDef
                else:
                    r += htmltext('<h3>%s</h3>') % _('Original card')
                    object_class = CardDef
                try:
                    orig_formdata = (
                        object_class.get(extra_context.get('orig_formdef_id'))
                        .data_class()
                        .get(extra_context.get('orig_formdata_id'))
                    )
                except KeyError:
                    r += htmltext('<p>%s</p>') % _('(deleted)')
                else:
                    r += htmltext('<p><a class="extra-context--orig-data" href="%s">%s</a></p>') % (
                        orig_formdata.get_url(backoffice=True),
                        orig_formdata.get_display_label(),
                    )
        return r.getvalue()

    def get_extra_submission_channel_bar(self):
        formdata = self.filled
        r = TemplateIO(html=True)
        if formdata.submission_channel:
            extra_context = formdata.submission_context or {}
            r += htmltext('<h3>%s</h3>') % '%s: %s' % (_('Channel'), formdata.get_submission_channel_label())
            if extra_context.get('caller'):
                r += htmltext('<h3>%s: %s</h3>') % (_('Phone'), extra_context['caller'])
            if extra_context.get('thumbnail_url'):
                r += htmltext('<p class="thumbnail"><img src="%s" alt=""/></p>') % extra_context.get(
                    'thumbnail_url'
                )
            if extra_context.get('mail_url'):
                r += htmltext('<p><a href="%s">%s</a></p>') % (extra_context.get('mail_url'), _('Open'))
            if extra_context.get('comments'):
                r += htmltext('<h3>%s</h3>') % _('Comments')
                r += htmltext('<p>%s</p>') % extra_context.get('comments')
            if extra_context.get('summary_url'):
                r += htmltext('<div data-content-url="%s"></div>' % (extra_context.get('summary_url')))
            r += htmltext('</div>')  # closes .extra-context from get_extra_submission_context_bar

        return r.getvalue()

    def get_extra_submission_user_id_bar(self, parent):
        formdata = self.filled
        r = TemplateIO(html=True)
        if formdata and formdata.user_id and formdata.get_user():
            r += htmltext('<div class="extra-context sidebar--user">')
            r += htmltext('<h3>%s</h3>') % _('Associated User')
            users_cfg = get_cfg('users', {})
            sidebar_user_template = users_cfg.get('sidebar_template')
            if sidebar_user_template:
                variables = get_publisher().substitutions.get_context_variables(mode='lazy')
                sidebar_user = htmltext(Template(sidebar_user_template).render(variables))
                if not sidebar_user.startswith('<'):
                    sidebar_user = htmltext('<p>%s</p>' % sidebar_user)
                r += sidebar_user
            else:
                r += htmltext('<p>%s</p>') % formdata.get_user().display_name
            r += htmltext('</div>')
        elif parent and parent.has_user_support and parent.edit_mode:
            r += self.get_extra_submission_user_selection_bar(parent=parent)
        return r.getvalue()

    def get_extra_submission_user_selection_bar(self, parent=None):
        r = TemplateIO(html=True)
        r += htmltext('<div class="submit-user-selection" style="display: none;">')
        get_response().add_javascript(['select2.js'])
        r += htmltext('<h3>%s</h3>') % _('Associated User')
        attrs = {
            'class': 'user-selection',
        }
        if self.formdef.submission_user_association == 'roles' and self.formdef.roles:
            attrs['data-users-api-roles'] = ','.join([str(x) for x in self.formdef.roles])
        r += htmltag('select', **attrs)
        if parent and parent.selected_user_id:
            r += htmltext('<option value="%s">%s</option>') % (
                parent.selected_user_id,
                get_publisher().user_class.get(parent.selected_user_id, ignore_errors=True),
            )
        r += htmltext('</select>')
        r += htmltext('</div>')

        return r.getvalue()

    def get_extra_geolocation_bar(self):
        formdata = self.filled
        r = TemplateIO(html=True)
        if formdata.formdef.geolocations and formdata.geolocations:
            r += htmltext('<div class="extra-context geolocations sidebar-geolocations">')
            for geoloc_key in formdata.formdef.geolocations:
                if geoloc_key not in formdata.geolocations:
                    continue
                r += htmltext('<h3>%s</h3>') % formdata.formdef.geolocations[geoloc_key]
                geoloc_value = formdata.geolocations[geoloc_key]
                map_widget = MapWidget(
                    'geoloc_%s' % geoloc_key, readonly=True, value=geoloc_value, render_br=False
                )
                r += map_widget.render()
            r += htmltext('</div>')
        return r.getvalue()

    def download_as_zip(self):
        formdata = self.filled
        zip_content = io.BytesIO()
        counter = {'value': 0}

        seen = set()

        def add_zip_file(upload, zip_file):
            file_key = f'{upload.file_digest()}-{upload.base_filename}'
            if file_key in seen:
                return
            seen.add(file_key)
            counter['value'] += 1
            filename = '%s_%s' % (counter['value'], upload.base_filename)
            zip_file.writestr(filename, upload.get_content())

        with zipfile.ZipFile(zip_content, 'w') as zip_file:
            for value in formdata.data.values():
                if isinstance(value, PicklableUpload):
                    add_zip_file(value, zip_file)
                if isinstance(value, dict) and isinstance(value.get('data'), list):
                    for subvalue in value.get('data'):
                        for subvalue_elem in subvalue.values():
                            if isinstance(subvalue_elem, PicklableUpload):
                                add_zip_file(subvalue_elem, zip_file)

        audit('download files', obj=formdata)
        response = get_response()
        response.set_content_type('application/zip')
        response.set_header(
            'content-disposition', 'attachment; filename=files-%s.zip' % formdata.get_display_id()
        )
        return zip_content.getvalue()

    def get_user_pending_forms(self):
        from wcs import sql

        formdata = self.filled
        r = TemplateIO(html=True)
        user_roles = [logged_users_role().id] + get_request().user.get_roles()
        criterias = [
            Equal('is_at_endpoint', False),
            Equal('user_id', str(formdata.user_id)),
            Intersects('concerned_roles_array', user_roles),
        ]
        formdatas = sql.AnyFormData.select(criterias, order_by='receipt_time')
        self.filled.related_user_forms = formdatas

        if formdatas:
            r += htmltext('<div class="extra-context user-pending-forms">')
            r += htmltext('<h3>%s</h3>') % _('User Pending Forms')
            categories = {}
            formdata_by_category = {}
            for formdata in formdatas:
                if formdata.formdef.category_id not in categories:
                    categories[formdata.formdef.category_id] = formdata.formdef.category
                    formdata_by_category[formdata.formdef.category_id] = []
                formdata_by_category[formdata.formdef.category_id].append(formdata)
            cats = list(categories.values())
            Category.sort_by_position(cats)
            if self.formdef.category_id in categories:
                # move current category to the top
                cats.remove(categories[self.formdef.category_id])
                cats.insert(0, categories[self.formdef.category_id])
            for cat in cats:
                if len(cats) > 1:
                    if cat is None:
                        r += htmltext('<h4>%s</h4>') % _('Misc')
                        cat_formdatas = formdata_by_category[None]
                    else:
                        r += htmltext('<h4>%s</h4>') % cat.name
                        cat_formdatas = formdata_by_category[str(cat.id)]
                else:
                    cat_formdatas = formdatas
                r += htmltext('<ul class="user-formdatas">')
                for formdata in cat_formdatas:
                    status = formdata.get_status()
                    if status:
                        status_label = status.name
                    else:
                        status_label = _('Unknown')
                    submit_date = misc.strftime(misc.date_format(), formdata.receipt_time)
                    if str(formdata.formdef_id) == str(self.formdef.id) and (
                        str(formdata.id) == str(self.filled.id)
                    ):
                        r += (
                            htmltext('<li class="self"><span class="formname">%s</span> ')
                            % formdata.formdef.name
                        )
                    else:
                        r += htmltext('<li><a href="%s">%s</a> ') % (
                            formdata.get_url(backoffice=True),
                            formdata.formdef.name,
                        )

                    r += htmltext('(<span class="id">%s</span>), ') % formdata.get_display_id()
                    r += htmltext('<span class="datetime">%s</span> <span class="status">(%s)</span>') % (
                        submit_date,
                        status_label,
                    )
                    if formdata.default_digest:
                        r += htmltext('<small>%s</small>') % formdata.default_digest
                    r += htmltext('</li>')
                r += htmltext('</ul>')
            r += htmltext('</div>')
        return r.getvalue()

    def get_lateral_block(self):
        r = TemplateIO(html=True)
        lateral_block = self.filled.get_lateral_block()
        if lateral_block:
            r += htmltext('<div class="lateral-block">')
            r += htmltext(lateral_block)
            r += htmltext('</div>')
        return r.getvalue()

    def test_tools_form(self):
        form = Form(use_tokens=True)
        options = [
            ('django-condition', _('Condition (Django)'), 'django-condition'),
            ('template', '%s / %s' % (_('Template'), _('Django Expression')), 'template'),
            ('html_template', _('HTML Template (WYSIWYG)'), 'html_template'),
        ]
        form.add(
            RadiobuttonsWidget,
            'test_mode',
            options=options,
            value='django-condition',
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio',
        )
        form.add(
            StringWidget,
            'django-condition',
            extra_css_class='grid-1-1',
            attrs={
                'data-dynamic-display-child-of': 'test_mode',
                'data-dynamic-display-value': 'django-condition',
            },
        )
        form.add(
            WysiwygTextWidget,
            'html_template',
            attrs={
                'data-dynamic-display-child-of': 'test_mode',
                'data-dynamic-display-value': 'html_template',
            },
        )
        form.add(
            TextWidget,
            'template',
            attrs={'data-dynamic-display-child-of': 'test_mode', 'data-dynamic-display-value': 'template'},
        )
        has_loop = False
        for status in self.formdef.workflow.possible_status:
            has_loop |= bool(status.loop_items_template)
        if has_loop:
            form.add(
                StringWidget,
                'template_loop',
                title=_('Template to be looped on'),
                attrs={
                    'data-dynamic-display-child-of': 'test_mode',
                    'data-dynamic-display-value': 'template',
                },
            )
            form.add(
                StringWidget,
                'html_template_loop',
                title=_('Template to be looped on'),
                attrs={
                    'data-dynamic-display-child-of': 'test_mode',
                    'data-dynamic-display-value': 'html_template',
                },
            )
        form.add_submit('submit', _('Evaluate'))
        return form

    def get_inspect_error_message(self, exception):
        if hasattr(exception, 'get_error_message'):
            # dedicated message
            return htmltext('<p>%s</p>') % exception.get_error_message()
        # generic exception
        try:
            error_message = htmltext('<code>%s: %s</code>') % (
                exception.__class__.__name__,
                str(exception),
            )
        except UnicodeEncodeError:
            error_message = htmltext('<code>%s</code>') % repr(exception)
        return htmltext('<p>%s %s</p>') % (_('Error message:'), error_message)

    def test_tool_result(self, form):
        if not form.is_submitted() or form.has_errors():
            return False

        r = TemplateIO(html=True)
        get_request().inspect_mode = True

        # show test result
        test_mode = form.get_widget('test_mode').parse()
        if test_mode == 'django-condition':
            condition_value = form.get_widget(test_mode).parse()
            condition = Condition(
                {'value': condition_value, 'type': test_mode.split('-')[0]},
                record_errors=False,
            )
            try:
                result = condition.unsafe_evaluate()
            except Exception as exception:
                r += htmltext('<div class="errornotice">')
                r += htmltext('<p>%s</p>') % _('Failed to evaluate condition.')
                r += self.get_inspect_error_message(exception)
                if isinstance(exception, DjangoTemplateSyntaxError) and Template.is_template_string(
                    condition_value, ezt_support=False
                ):
                    r += htmltext('<p class="hint">%s</p>') % _(
                        'This tool expects a condition, not a complete template.'
                    )
                r += htmltext('</div>')
            else:
                r += htmltext('<div class="test-tool-result infonotice">')
                r += htmltext('<h3>%s</h3>') % _('Condition result:')
                r += htmltext('<p><span class="result-%s">%s</span>') % (
                    str(bool(result)).lower(),
                    _('True') if result else _('False'),
                )
                r += htmltext('</p>')
                r += htmltext('</div>')
            return r.getvalue()

        # (HTML) template
        loop_items = None
        widget = form.get_widget('template_loop' if test_mode == 'template' else 'html_template_loop')
        if widget and not widget.has_error() and widget.parse():
            loop_template = widget.parse()
            with get_publisher().complex_data():
                try:
                    value = WorkflowStatusItem.compute(
                        loop_template,
                        formdata=self.filled,
                        raises=True,
                        allow_complex=True,
                        record_errors=False,
                    )
                except Exception as exception:
                    r += htmltext('<div class="errornotice">')
                    r += htmltext('<p>%s</p>') % _('Failed to evaluate loop template.')
                    r += self.get_inspect_error_message(exception)
                    r += htmltext('</div>')
                    return r.getvalue()

                loop_items = get_publisher().get_cached_complex_data(value, loop_context=True)
                try:
                    iter(loop_items)
                except TypeError:
                    r += htmltext('<div class="test-tool-result errornotice">')
                    r += htmltext('<p>%s</p>') % (_('Invalid value to be looped on (%r)') % loop_items)
                    r += htmltext('</div>')
                    return r.getvalue()

        loop_iterations = 0
        for i, loop_item in enumerate(loop_items if loop_items is not None else [True]):
            loop_iterations += 1

            if i == 5:
                r += htmltext('<div class="infonotice">')
                r += htmltext('<p>%s</p>') % _('Loop test limited to 5 iterations')
                r += htmltext('</div>')
                break

            if loop_item and loop_items:
                get_publisher().substitutions.feed(
                    WorkflowStatus.get_status_loop(index=i, items=loop_items, item=loop_item)
                )

            if test_mode == 'template':
                try:
                    template = form.get_widget('template').parse() or ''
                    with get_publisher().complex_data():
                        result = WorkflowStatusItem.compute(
                            template, raises=True, record_errors=False, allow_complex=True
                        )
                        has_complex_result = get_publisher().has_cached_complex_data(result)
                        complex_result = get_publisher().get_cached_complex_data(result)
                        complex_iterable_result = get_publisher().get_cached_complex_data(
                            result, loop_context=True
                        )
                        result = re.sub(r'[\uE000-\uF8FF]', '', result)
                except Exception as exception:
                    r += htmltext('<div class="errornotice">')
                    r += htmltext('<p>%s</p>') % _('Failed to evaluate template.')
                    r += self.get_inspect_error_message(exception)
                    r += htmltext('</div>')
                    break
                else:
                    r += htmltext('<div class="test-tool-result infonotice">')
                    r += htmltext('<h3>%s</h3>') % _('Template rendering:')
                    if result and result[0] == '<':  # seems to be HTML
                        r += htmltext('<div class="test-tool-result-html">')
                        cleaner = Cleaner(
                            tags=WysiwygTextWidget.ALL_TAGS,
                            css_sanitizer=CSSSanitizer(allowed_css_properties=WysiwygTextWidget.ALL_STYLES),
                            attributes=WysiwygTextWidget.ALL_ATTRS,
                        )
                        cleaned_result = cleaner.clean(result)
                        r += htmltext(cleaned_result)
                        r += htmltext('</div>')
                        r += htmltext('<h3>%s</h3>') % _('HTML Source:')
                        r += htmltext('<pre class="test-tool-result-plain">%s</pre>') % result
                    else:
                        r += htmltext('<div class="test-tool-result-plain">%s</div>') % result
                    if has_complex_result:
                        r += htmltext('<h3>%s</h3>') % _('Also rendered as an object:')
                        r += htmltext('<div class="test-tool-result-plain">%s (%s)</div>') % (
                            str(complex_result),
                            get_type_name(complex_result),
                        )
                        if isinstance(complex_result, list):
                            r += htmltext('<ul class="test-tool-lazylist-details">')
                            r += htmltext('<li>%s %s</li>') % (_('Number of items:'), len(complex_result))
                            if len(complex_result):
                                r += htmltext('<li>%s ') % _('First items:')
                                r += ', '.join([str(x) for x in complex_result[:5]])
                                r += htmltext('</li>')
                            r += htmltext('</ul>')
                        if complex_iterable_result != complex_result:
                            r += htmltext('<h3>%s</h3>') % _('Also rendered as an iterable:')
                            r += htmltext('<div class="test-tool-result-plain">%s (%s)</div>') % (
                                str(complex_iterable_result),
                                get_type_name(complex_iterable_result),
                            )
                    r += htmltext('</div>')
            elif test_mode == 'html_template':
                try:
                    html_template = form.get_widget('html_template').parse() or ''
                    result = template_on_formdata(
                        self.filled, html_template, raises=True, ezt_format=ezt.FORMAT_HTML
                    )
                except Exception as exception:
                    r += htmltext('<div class="errornotice">')
                    r += htmltext('<p>%s</p>') % _('Failed to evaluate HTML template.')
                    r += self.get_inspect_error_message(exception)
                    r += htmltext('</div>')
                    break
                else:
                    r += htmltext('<div class="test-tool-result infonotice">')
                    r += htmltext('<h3>%s</h3>') % _('Template rendering:')
                    r += htmltext('<div class="test-tool-result-html">')
                    r += htmltext(result)
                    r += htmltext('</div>')
                    r += htmltext('<h3>%s</h3>') % _('HTML Source:')
                    r += htmltext('<pre class="test-tool-result-plain">%s</pre>') % result
                    r += htmltext('</div>')

        if loop_iterations == 0:
            r += htmltext('<div class="test-tool-result infonotice">')
            r += htmltext('<p>%s</p>') % _('Loop template didn\'t provide any element.')
            r += htmltext('</div>')

        return r.getvalue()

    def inspect(self):
        if not self.can_go_in_inspector():
            raise errors.AccessForbiddenError()
        if self.filled.is_draft() and not get_publisher().has_site_option('allow-draft-inspect'):
            raise errors.AccessForbiddenError()
        get_response().breadcrumb.append(('inspect', _('Data Inspector')))
        get_response().set_title(self.formdef.name)

        context = {}

        context['actions'] = actions = []

        if self.formdef._names == 'formdefs':
            if get_publisher().get_backoffice_root().is_accessible('forms'):
                actions.append({'url': self.formdef.get_admin_url(), 'label': _('View Form')})
        elif self.formdef._names == 'carddefs':
            if get_publisher().get_backoffice_root().is_accessible('cards'):
                actions.append({'url': self.formdef.get_admin_url(), 'label': _('View Card')})
        if get_publisher().get_backoffice_root().is_accessible('workflows'):
            actions.append({'url': self.formdef.workflow.get_admin_url(), 'label': _('View Workflow')})

        context['html_form'] = self.test_tools_form()
        context['test_tool_result'] = self.test_tool_result(context['html_form'])
        context['view'] = self

        self.workflow_traces = self.filled.get_workflow_traces()
        context['has_tracing'] = bool(self.workflow_traces)

        context['has_markers_stack'] = bool('_markers_stack' in (self.filled.workflow_data or {}))
        self.relations = list(self.filled.iter_target_datas())
        context['has_relations'] = bool(self.relations)

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/formdata-inspect.html'], context=context
        )

    def inspect_variables(self):
        r = TemplateIO(html=True)
        substvars = CompatibilityNamesDict()
        substvars.update(self.filled.get_substitution_variables())

        def safe(v):
            try:
                v = force_str(v)
            except Exception:
                v = repr(v)
            return v

        access_to_admin_forms = get_publisher().get_backoffice_root().is_global_accessible('forms')
        access_to_formdef = self.formdef.has_admin_access(get_request().user)
        access_to_workflow = self.formdef.workflow.has_admin_access(get_request().user)

        inspect_expanded_trees = (
            get_request().form.get('expand').split(',') if get_request().form.get('expand') else []
        )

        with get_publisher().inspect_recurse_skip(inspect_expanded_trees):
            keys = substvars.get_flat_keys()

        for k in sorted(keys):
            if not k.startswith('form_'):
                # do not display legacy variables
                continue
            k = safe(k)
            with get_publisher().inspect_recurse_skip(inspect_expanded_trees):
                v = substvars[k]
            breaking_k = htmlescape(k).replace('_', htmltext('_<wbr/>'))
            if isinstance(v, SubtreeVar):
                r += htmltext('<li><code title="%s">%s</code>') % (k, breaking_k)
                expand_value = ','.join(inspect_expanded_trees + [k])
                r += htmltext(
                    '<div class="value"><a class="inspect-expand-variable" href="?expand=%s">(%s)</a>'
                ) % (expand_value, _('expand this variable'))
            elif isinstance(v, LazyFieldVar):
                r += htmltext('<li><code title="%s"><span class="varname">%s</span>') % (k, breaking_k)
                if v._formdata == self.filled:
                    field_url = None
                    if v._field.id.startswith('bo'):
                        if access_to_workflow:
                            field_url = '%s%s/' % (
                                self.formdef.workflow.backoffice_fields_formdef.get_admin_url(),
                                v._field.id,
                            )
                    elif v._field_kwargs.get('parent_field') is not None:
                        if access_to_admin_forms:
                            field_url = '%s%s/' % (
                                v._field_kwargs['parent_field'].block.get_admin_url(),
                                v._field.id,
                            )
                    elif access_to_formdef:
                        field_url = '%sfields/%s/' % (self.formdef.get_admin_url(), v._field.id)
                    if field_url:
                        r += htmltext(' <a title="%s" href="%s"></a>' % (v._field.label, field_url))
                r += htmltext('</code>')
                r += htmltext('  <div class="value"><span>%s</span>') % misc.mark_spaces(v)
                unlazy_value = misc.unlazy(v)
                if not isinstance(unlazy_value, str):
                    r += htmltext(' <span class="type">(%s)</span>') % get_type_name(unlazy_value)
            elif isinstance(v, (types.FunctionType, types.MethodType)):
                continue
            elif k.endswith('form_parent') and isinstance(v, CompatibilityNamesDict) and ('form' in v):
                r += htmltext('<li><code title="%s">%s_…</code>') % (k, breaking_k)
                r += htmltext(
                    '  <div class="value"><span>%(caption)s '
                    '(<a href="%(inspect_url)s">%(display_name)s</a>)</span>'
                ) % {
                    'caption': htmltext('<var>%s</var>') % _('variables from parent\'s request'),
                    'inspect_url': v['form'].backoffice_url + 'inspect',
                    'display_name': v['form_display_name'],
                }
            elif hasattr(v, 'inspect_keys') and not getattr(v, 'include_in_inspect', False):
                # skip as there are expanded identifiers
                continue
            else:
                if isinstance(v, dict):
                    # only display dictionaries if they have invalid keys
                    # (otherwise the expanded identifiers are a better way
                    # to get to the values).
                    if all(CompatibilityNamesDict.valid_key_regex.match(k) for k in v):
                        continue
                r += htmltext('<li><code class="varname" title="%s">%s</code>') % (k, breaking_k)
                if isinstance(v, list):
                    # custom behaviour for lists so strings within can be displayed
                    # with a dedicated repr function.
                    r += htmltext('<div class="value"><span>[')

                    def custom_repr(var):
                        # replace non breaking spaces from strings, so translated
                        # error messages from webservice calls are more readable.
                        if isinstance(var, str):
                            var = var.replace('\xa0', ' ')
                        return repr(var)

                    r += ', '.join(custom_repr(x) for x in v)
                    r += htmltext(']</span>')
                else:
                    if k in ('form_details', 'form_evolution'):
                        # do not mark spaces in those variables
                        r += htmltext('  <div class="value"><span>%s</span>') % ellipsize(safe(v), 10000)
                    else:
                        r += htmltext('  <div class="value"><span>%s</span>') % misc.mark_spaces(
                            ellipsize(safe(v), 10000)
                        )
                if not isinstance(v, str):
                    r += htmltext(' <span class="type">(%s)</span>') % get_type_name(v)
            r += htmltext('</div></li>')
        return r.getvalue()

    def inspect_functions(self):
        r = TemplateIO(html=True)
        # assigned functions
        if self.formdef.workflow.roles:
            workflow = self.formdef.workflow
            for key, label in (workflow.roles or {}).items():
                r += htmltext('<li data-function-key="%s"><span class="label">%s</span>') % (key, label)
                r += htmltext('<div class="value">')
                acting_role_ids = self.filled.get_function_roles(key)
                acting_role_names = []
                for acting_role_id in acting_role_ids:
                    try:
                        if acting_role_id.startswith('_user:'):
                            acting_role = get_publisher().user_class.get(acting_role_id.split(':')[1])
                            acting_role_names.append(acting_role.name)
                        else:
                            acting_role = get_publisher().role_class.get(acting_role_id)
                            if key not in (self.filled.workflow_roles or {}):
                                suffix = ' (%s)' % _('default')
                            else:
                                suffix = ''
                            acting_role_names.append(acting_role.get_as_inline_html() + suffix)
                    except KeyError:
                        acting_role_names.append('%s (%s)' % (acting_role_id, _('deleted')))
                if acting_role_names:
                    acting_role_names.sort()
                    r += htmltext(', ').join(acting_role_names)
                else:
                    r += htmltext('<span class="unset">%s</span>') % _('unset')
                r += htmltext('</div>')
                r += htmltext('</li>\n')
        return r.getvalue()

    def inspect_tracing(self):
        r = TemplateIO(html=True)
        action_classes = {x.key: x.description for x in item_classes}
        last_status_id = None
        global_event = None
        for trace in self.workflow_traces:
            if trace.event:
                global_event = trace if trace.is_global_event() else None
                r += trace.print_event(formdata=self.filled, global_event=global_event)

            if trace.status_id != last_status_id:
                last_status_id = trace.status_id
                r += htmltext(trace.print_status(filled=self.filled))

            if trace.action_item_key:
                r += htmltext(
                    trace.print_action(
                        action_classes=action_classes, filled=self.filled, global_event=global_event
                    )
                )

        return r.getvalue()

    def inspect_markers_stack(self):
        r = TemplateIO(html=True)
        for marker in reversed(self.filled.workflow_data['_markers_stack']):
            status = self.filled.get_status(marker['status_id'])
            if status:
                r += htmltext('<li><span class="status">%s</span></li>') % status.name
            else:
                r += htmltext('<li><span class="status">%s</span></li>') % _('Unknown')
        return r.getvalue()

    def inspect_relations(self):
        r = TemplateIO(html=True)
        children = self.relations

        for child, origin in children:
            r += htmltext('<li class="biglistitem"><a class="biglistitem--content" ')
            if isinstance(child, str):
                r += htmltext('href="">%s (%s)</a></li>') % (child, origin)
            else:
                r += htmltext('href="%s">%s (%s)</a></li>') % (
                    child.get_url(backoffice=True),
                    child.get_display_name(),
                    origin,
                )

        return r.getvalue()

    def inspect_tool(self):
        if not (
            get_publisher().get_backoffice_root().is_accessible('forms')
            or get_publisher().get_backoffice_root().is_accessible('workflows')
        ):
            raise errors.AccessForbiddenError()
        get_response().raw = True
        form = self.test_tools_form()
        resp = self.test_tool_result(form)
        if resp is False:
            resp = ''
        else:
            get_response().headers = {'X-form-token': get_session().create_form_token()}
        return resp


def do_graphs_section(period_start=None, period_end=None, criterias=None):
    from wcs import sql

    r = TemplateIO(html=True)
    monthly_totals = sql.get_monthly_totals(period_start, period_end, criterias)[-12:]
    yearly_totals = sql.get_yearly_totals(period_start, period_end, criterias)[-10:]

    if not monthly_totals:
        monthly_totals = [('%s-%s' % datetime.date.today().timetuple()[:2], 0)]
    if not yearly_totals:
        yearly_totals = [(datetime.date.today().year, 0)]

    weekday_totals = sql.get_weekday_totals(period_start, period_end, criterias)
    hour_totals = sql.get_hour_totals(period_start, period_end, criterias)

    r += htmltext(
        '''<script>
var weekday_line = %(weekday_line)s;
var hour_line = %(hour_line)s;
var month_line = %(month_line)s;
var year_line = %(year_line)s;
</script>'''
        % {
            'weekday_line': json.dumps(weekday_totals, cls=misc.JSONEncoder),
            'hour_line': json.dumps(hour_totals, cls=misc.JSONEncoder),
            'month_line': json.dumps(monthly_totals, cls=misc.JSONEncoder),
            'year_line': json.dumps(yearly_totals, cls=misc.JSONEncoder),
        }
    )

    if len(yearly_totals) > 1:
        r += htmltext('<h3>%s</h3>') % _('Submissions by year')
        r += htmltext('<div id="chart_years" style="height:160px; width:100%;"></div>')

    r += htmltext('<h3>%s</h3>') % _('Submissions by month')
    r += htmltext('<div id="chart_months" style="height:160px; width:100%;"></div>')
    r += htmltext('<h3>%s</h3>') % _('Submissions by weekday')
    r += htmltext('<div id="chart_weekdays" style="height:160px; width:100%;"></div>')
    r += htmltext('<h3>%s</h3>') % _('Submissions by hour')
    r += htmltext('<div id="chart_hours" style="height:160px; width:100%;"></div>')

    get_response().add_javascript(
        [
            'jquery.js',
            'jqplot/jquery.jqplot.min.js',
            'jqplot/plugins/jqplot.canvasTextRenderer.min.js',
            'jqplot/plugins/jqplot.canvasAxisLabelRenderer.min.js',
            'jqplot/plugins/jqplot.canvasAxisTickRenderer.min.js',
            'jqplot/plugins/jqplot.categoryAxisRenderer.min.js',
            'jqplot/plugins/jqplot.barRenderer.min.js',
        ]
    )

    get_response().add_javascript_code(
        '''
function wcs_draw_graphs() {
$.jqplot ('chart_weekdays', [weekday_line], {
series:[{renderer:$.jqplot.BarRenderer}],
axesDefaults: {
tickRenderer: $.jqplot.CanvasAxisTickRenderer,
tickOptions: { angle: -30, }
},
axes: { xaxis: { renderer: $.jqplot.CategoryAxisRenderer } }
});

$.jqplot ('chart_hours', [hour_line], {
axesDefaults: {
tickRenderer: $.jqplot.CanvasAxisTickRenderer,
tickOptions: { angle: -30, }
},
axes: { xaxis: { renderer: $.jqplot.CategoryAxisRenderer }, yaxis: {min: 0} }
});

$.jqplot ('chart_months', [month_line], {
axesDefaults: {
tickRenderer: $.jqplot.CanvasAxisTickRenderer,
tickOptions: { angle: -30, }
},
axes: { xaxis: { renderer: $.jqplot.CategoryAxisRenderer }, yaxis: {min: 0} }
});

if ($('#chart_years').length) {
$.jqplot ('chart_years', [year_line], {
series:[{renderer:$.jqplot.BarRenderer}],
axesDefaults: {
tickRenderer: $.jqplot.CanvasAxisTickRenderer,
tickOptions: { angle: -30, }
},
axes: { xaxis: { renderer: $.jqplot.CategoryAxisRenderer }, yaxis: {min: 0} }
});
}
}

$(document).ready(function(){
  wcs_draw_graphs();
});
    '''
    )
    return r.getvalue()


def get_global_criteria(request, parsed_values=None):
    """
    Parses the request query string and returns a list of criterias suitable
    for select() usage. The parsed_values parameter can be given a dictionary,
    to be filled with the parsed values.
    """
    criterias = [StrictNotEqual('status', 'draft')]
    try:
        period_start = misc.get_as_datetime(request.form.get('start')).timetuple()
        criterias.append(GreaterOrEqual('receipt_time', period_start))
        parsed_values['period_start'] = period_start
    except (ValueError, TypeError):
        pass

    try:
        period_end = misc.get_as_datetime(request.form.get('end')).timetuple()
        criterias.append(LessOrEqual('receipt_time', period_end))
        parsed_values['period_end'] = period_end
    except (ValueError, TypeError):
        pass

    return criterias


def format_time(t, units=2):
    days = int(t / 86400)
    hours = int((t - days * 86400) / 3600)
    minutes = int((t - days * 86400 - hours * 3600) / 60)
    seconds = t % 60
    if units == 1:
        if days:
            return _('%d day(s)') % days
        if hours:
            return _('%d hour(s)') % hours
        if minutes:
            return _('%d minute(s)') % minutes
    elif units == 2:
        if days:
            return _('%(days)d day(s) and %(hours)d hour(s)') % {'days': days, 'hours': hours}
        if hours:
            return _('%(hours)d hour(s) and %(minutes)d minute(s)') % {'hours': hours, 'minutes': minutes}
        if minutes:
            return _('%(minutes)d minute(s) and %(seconds)d seconds') % {
                'minutes': minutes,
                'seconds': seconds,
            }
    return _('%d seconds') % seconds


class MassActionAfterJob(AfterJob):
    def __init__(self, formdef, **kwargs):
        super().__init__(formdef_class=formdef.__class__, formdef_id=formdef.id, **kwargs)
        self.query_string = kwargs.get('query_string')

    def __getstate__(self):
        obj_dict = super().__getstate__()
        obj_dict.pop('oldest_lazy_form', None)
        return obj_dict

    def execute(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        item_ids = self.kwargs['item_ids']
        action_id = self.kwargs['action_id']
        user = get_publisher().user_class.get(self.kwargs['user_id'])

        multi_actions = FormPage.get_multi_actions(formdef, user)
        for action in multi_actions:
            if action['action'].id == action_id:
                break
        else:
            # action not found
            return

        if not hasattr(self, 'processed_ids'):
            self.processed_ids = {}

        self.total_count = len(item_ids)
        self.store()

        self.skipped_formdata_ids = {}  # str(formdata_id): idx
        self.oldest_lazy_form = None

        publisher = get_publisher()

        for i, formdata_id in enumerate(item_ids):
            if str(formdata_id) not in self.skipped_formdata_ids:
                self.execute_one(publisher, action, formdef, user, i, formdata_id)

        # second chance for formdata that were skipped
        for formdata_id, idx in list(self.skipped_formdata_ids.items()):
            self.execute_one(publisher, action, formdef, user, idx, formdata_id)

        self.store()

    def execute_one(self, publisher, action, formdef, user, i, formdata_id):
        # do not load all formdatas at once as they can be modified during the loop
        # (by external workflow calls for example)
        if formdata_id in self.processed_ids:
            return
        formdata = formdef.data_class().get(formdata_id, ignore_errors=True)
        if not formdata:
            self.processed_ids[formdata_id] = now()
            return
        if formdata.workflow_processing_timestamp:
            self.skipped_formdata_ids[str(formdata.id)] = i
            self.store()
            return
        if self.oldest_lazy_form is None:
            self.oldest_lazy_form = formdata.get_as_lazy()
        publisher.reset_formdata_state()
        publisher.substitutions.feed(user)
        publisher.substitutions.feed(formdef)
        publisher.substitutions.feed(formdata)
        publisher.substitutions.feed(
            {
                'oldest_form': self.oldest_lazy_form,
                'mass_action_index': i,
                'mass_action_length': self.total_count,
            }
        )
        if getattr(action['action'], 'status_action', False):
            # manual jump action
            if formdata.status.removeprefix('wf-') == action['statuses'][0] and action[
                'action'
            ].action.check_condition(formdata):
                from wcs.wf.jump import jump_and_perform

                formdata.record_workflow_event('mass-jump', action_item_id=action['action'].action.id)
                jump_and_perform(formdata, action['action'].action)
        else:
            # global action
            if action['action'].check_executable(formdata, user):
                formdata.record_workflow_event('global-action-mass', global_action_id=action['action'].id)
                formdata.perform_global_action(action['action'].id, user)
        self.processed_ids[formdata_id] = now()
        self.skipped_formdata_ids.pop(str(formdata.id), None)
        self.refresh_column('abort_requested')
        self.store()  # self.processed_ids must be saved
        self.increment_count()

    def done_action_url(self):
        return self.kwargs['return_url']

    def done_action_label(self):
        return _('Back to Listing')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}


class CsvExportAfterJob(AfterJob):
    label = _('Exporting to CSV file')

    def __init__(self, formdef, **kwargs):
        user = kwargs.pop('user', None)
        if user and user.is_api_user:
            kwargs['user_is_api_user'] = True
            kwargs['user_id'] = user.api_access.id
        else:
            kwargs['user_is_api_user'] = False
            kwargs['user_id'] = user.id if user else None
        super().__init__(formdef_class=formdef.__class__, formdef_id=formdef.id, **kwargs)
        self.file_name = '%s.csv' % formdef.url_name

    def csv_tuple_heading(self, fields):
        heading_fields = []  # '#id', _('time'), _('userlabel'), _('status')]
        for field in fields:
            if getattr(field, 'block_field', None):
                heading = field.block_field.get_csv_heading(subfield=field)
            else:
                heading = field.get_csv_heading()
            heading_fields.extend(heading)
        return heading_fields

    def get_spreadsheet_line(self, fields, data):
        elements = []
        for field in fields:
            if getattr(field, 'block_field', None):
                nb_items = field.block_field.get_max_items()
                block_data = data.data.get(field.block_field.id)
                nb_columns = len(field.block_field.get_csv_heading(subfield=field))
                block_elements = []
                for i in range(nb_items):
                    try:
                        block_row_data = block_data['data'][i]
                    except (KeyError, IndexError, TypeError):
                        break
                    field_value = block_row_data.get(field.id)
                    if field.key == 'file' and field_value:
                        field_value = field_value.base_filename
                    elif field.key in ['date', 'bool']:
                        field_value = field.get_view_value(field_value)
                    display_value = block_row_data.get(f'{field.id}_display')
                    structured_value = block_row_data.get(f'{field.id}_structured')
                    for value in field.get_csv_value(
                        field_value,
                        display_value=display_value,
                        structured_value=structured_value,
                        subfield=field,
                    ):
                        block_elements.append({'field': field, 'value': value, 'native_value': field_value})
                if len(block_elements) < nb_columns:
                    # fill with blank columns
                    block_elements.extend(
                        [{'value': '', 'field': field, 'native_value': None}]
                        * (nb_columns - len(block_elements))
                    )
                elements.extend(block_elements)
                continue

            element = data.get_field_view_value(field) or ''
            display_value = None
            structured_value = None
            if field.store_display_value:
                display_value = data.data.get('%s_display' % field.id) or ''
            if field.store_structured_value:
                structured_value = data.data.get('%s_structured' % field.id)
            for value in field.get_csv_value(
                element, display_value=display_value, structured_value=structured_value
            ):
                elements.append({'field': field, 'value': value, 'native_value': element})
        return elements

    def execute(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        selected_filter = self.kwargs['selected_filter']
        selected_filter_operator = self.kwargs['selected_filter_operator']
        fields = self.kwargs['fields']
        query = self.kwargs['query']
        criterias = self.kwargs['criterias']
        order_by = self.kwargs['order_by']
        if self.kwargs['user_is_api_user']:
            user = ApiAccess.get(self.kwargs['user_id']).get_as_api_user()
        else:
            user = get_publisher().user_class.get(self.kwargs['user_id'])

        items, total_count = FormDefUI(formdef).get_listing_items(
            fields,
            selected_filter,
            selected_filter_operator,
            user=user,
            query=query,
            criterias=criterias,
            order_by=order_by,
        )
        self.total_count = total_count
        self.store()

        return self.create_export(formdef, fields, items, total_count)

    def create_export(self, formdef, fields, items, total_count):
        output = tempfile.TemporaryFile(mode='w+t')
        tuple_heading = self.csv_tuple_heading(fields)
        if len(tuple_heading) == 1:
            csv_output = csv.writer(output, quoting=csv.QUOTE_NONE, delimiter='\ue000', escapechar='\ue001')
        else:
            csv_output = csv.writer(output, quoting=csv.QUOTE_ALL)

        if not self.kwargs.get('skip_header_line'):
            csv_output.writerow(tuple_heading)

        for filled in items:
            csv_output.writerow(tuple(x['value'] for x in self.get_spreadsheet_line(fields, filled)))
            self.increment_count()

        output.seek(0)

        self.content_type = 'text/csv'
        self.result_file = PicklableUpload(self.file_name, self.content_type)
        self.result_file.receive(iter(lambda: output.read(2**18).encode(), b''))
        output.close()
        self.store()

    def done_action_url(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        return formdef.get_url(backoffice=True) + 'export?download=%s' % self.id

    def done_action_label(self):
        return _('Download Export')

    def done_button_attributes(self):
        return {'download': self.file_name}


class OdsExportAfterJob(CsvExportAfterJob):
    label = _('Exporting to ODS file')

    def __init__(self, formdef, **kwargs):
        super().__init__(formdef=formdef, **kwargs)
        self.file_name = '%s.ods' % formdef.url_name

    def create_export(self, formdef, fields, items, total_count):
        workbook = ods.Workbook(encoding='utf-8')
        ws = workbook.add_sheet(formdef.name)

        header_line_counter = 0
        if not self.kwargs.get('skip_header_line'):
            header_line_counter = 1
            for i, field in enumerate(self.csv_tuple_heading(fields)):
                ws.write(0, i, field)

        for i, formdata in enumerate(items):
            for j, item in enumerate(self.get_spreadsheet_line(fields, formdata)):
                ws.write(
                    i + header_line_counter,
                    j,
                    item['value'],
                    formdata=formdata,
                    data_field=item['field'],
                    native_value=item['native_value'],
                )
            self.increment_count()

        with tempfile.TemporaryFile() as fd:
            workbook.save(fd)
            fd.seek(0)
            self.content_type = 'application/vnd.oasis.opendocument.spreadsheet'
            self.result_file = PicklableUpload(self.file_name, self.content_type)
            self.result_file.receive(iter(lambda: fd.read(2**18), b''))  # read by 256K chunks
        self.store()


class JsonFileExportAfterJob(CsvExportAfterJob):
    label = _('Exporting to JSON file')

    def __init__(self, formdef, **kwargs):
        super().__init__(formdef=formdef, **kwargs)
        self.file_name = '%s.json' % formdef.url_name

    def create_json_export(
        self,
        items,
        *,
        user,
        anonymise,
        digest_key,
        include_evolution,
        include_files,
        include_roles,
        include_submission,
        include_fields,
        include_user,
        include_unnamed_fields,
        include_workflow,
        include_workflow_data,
        include_actions,
        values_at=None,
        related_fields=None,
    ):
        # noqa pylint: disable=too-many-arguments
        if values_at:
            try:
                values_at = datetime.datetime.fromisoformat(values_at)
                if is_naive(values_at):
                    values_at = make_aware(values_at)
            except ValueError:
                raise RequestError(_('Invalid value "%s" for "at".') % values_at)

            # disable support for related_fields when values_at is sued
            related_fields = []

        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        if include_evolution or include_workflow:
            # 'include-workflow' only need the status column of Evolutions
            # 'include-evolution' needs the parts column
            items = formdef.data_class().prefetch_evolutions(items, include_parts=include_evolution)
        items, prefetched_users = formdef.data_class().prefetch_users(items)

        prefetched_roles = {}
        if include_roles:
            items, prefetched_roles = formdef.data_class().prefetch_roles(items)

        for formdata in items:
            try:
                data = formdata.get_json_export_dict(
                    anonymise=anonymise,
                    user=user,
                    digest_key=digest_key,
                    prefetched_users=prefetched_users,
                    prefetched_roles=prefetched_roles,
                    include_evolution=include_evolution,
                    include_files=include_files,
                    include_roles=include_roles,
                    include_submission=include_submission,
                    include_fields=include_fields,
                    include_user=include_user,
                    include_unnamed_fields=include_unnamed_fields,
                    include_workflow=include_workflow,
                    include_workflow_data=include_workflow_data,
                    include_actions=include_actions,
                    values_at=values_at,
                )
                if include_fields and related_fields:
                    data['related_fields'] = {}
                    for related_field in related_fields:
                        if not (related_field.parent_field.varname and related_field.related_field.varname):
                            continue
                        for suffix in ('', '_display', '_structured'):
                            if f'{related_field.contextual_id}{suffix}' in formdata.data:
                                if related_field.parent_field.varname not in data['related_fields']:
                                    data['related_fields'][related_field.parent_field.varname] = {}
                                data['related_fields'][related_field.parent_field.varname][
                                    f'{related_field.related_field.varname}{suffix}'
                                ] = formdata.data[f'{related_field.contextual_id}{suffix}']
            except NoContentSnapshotAt:
                continue
            data.pop('digests')
            if digest_key:
                data['digest'] = (formdata.digests or {}).get(digest_key)
            yield data
            self.increment_count()

    def create_export(self, formdef, fields, items, total_count):
        self.content_type = 'application/json'
        with tempfile.TemporaryFile() as fd:
            fd.write(b'{"data":[\n')
            first = True
            for data in self.create_json_export(
                items,
                user=None,
                anonymise=False,
                digest_key=None,
                include_evolution=False,
                include_files=True,
                include_roles=False,
                include_submission=True,
                include_fields=True,
                include_user=True,
                include_unnamed_fields=True,
                include_workflow=True,
                include_workflow_data=True,
                include_actions=False,
            ):
                if not first:
                    fd.write(b',\n')
                fd.write(json.dumps(data, indent=2, cls=misc.JSONEncoder).encode())
                first = False
            fd.write(b'\n]}')
            fd.seek(0)
            self.result_file = PicklableUpload(self.file_name, self.content_type)
            self.result_file.receive(iter(lambda: fd.read(2**18), b''))  # read by 256K chunks
        self.store()


class FakeField:
    # 2024-04-12, legacy class, for transition from FakeField to filter_fields.*
    # can be removed once all afterjobs referencing fake fields are removed.
    # (= 2 days after update)
    pass
