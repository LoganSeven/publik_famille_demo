# w.c.s. - web application for online forms
# Copyright (C) 2005-2012  Entr'ouvert
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
import collections.abc
import hashlib
import itertools
import json
import urllib.parse
import xml.etree.ElementTree as ET

from django.core.cache import cache
from django.template import TemplateSyntaxError, VariableDoesNotExist
from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request, get_response, get_session
from quixote.errors import RequestError
from quixote.html import TemplateIO

import wcs.sql

from .api_utils import sign_url_auto_orig
from .qommon import _, get_logger, misc, pgettext
from .qommon.form import (
    CompositeWidget,
    ComputedExpressionWidget,
    OptGroup,
    SingleSelectWidget,
    StringWidget,
    ValidationError,
)
from .qommon.humantime import seconds2humanduration
from .qommon.misc import get_variadic_url
from .qommon.storage import StoredObjectMixin
from .qommon.template import Template, TemplateError
from .qommon.xml_storage import XmlObjectMixin
from .utils import add_timing_mark

data_source_functions = {}


class NamedDataSourceImportError(Exception):
    pass


class DataSourceError(Exception):
    pass


def get_data_source_entry_from_user(user, role_prefetch=None):
    user_dict = user.get_substitution_variables(prefix='', role_prefetch=role_prefetch)
    del user_dict['user']
    user_dict['id'] = user.id
    user_dict['text'] = user.name
    return user_dict


class DataSourceSelectionWidget(CompositeWidget):
    def __init__(
        self,
        name,
        value=None,
        allowed_source_types=None,
        disallowed_source_types=None,
        allowed_external_type=None,
        **kwargs,
    ):
        if allowed_source_types is None:
            allowed_source_types = {'json', 'jsonp', 'geojson', 'named', 'cards', 'jsonvalue'}
        if get_publisher().has_site_option('disable-jsonp-sources') and 'jsonp' in allowed_source_types:
            allowed_source_types.remove('jsonp')
        if disallowed_source_types:
            allowed_source_types = allowed_source_types.difference(disallowed_source_types)

        CompositeWidget.__init__(self, name, value, **kwargs)

        if not value:
            value = {}

        options = [(None, _('None'), None)]

        if 'cards' in allowed_source_types:
            from wcs.carddef import CardDef
            from wcs.categories import CardDefCategory

            user = get_request().user
            cards_options = []
            for ds in CardDef.get_carddefs_as_data_source():
                option = [ds[2], ds[1], ds[2], {'carddef': ds[0]}]
                if ds[3] and (user.is_admin or ds[0].is_of_concern_for_user(user)):
                    option[3].update({'data-goto-url': '%s%s' % (ds[0].get_url(), ds[3].get_url_slug())})
                elif get_publisher().get_backoffice_root().is_accessible('cards'):
                    option[3].update({'data-goto-url': ds[0].get_admin_url()})
                option[3].update({'data-has-image': str(ds[0].has_image_field()).lower()})
                cards_options.append(option)
            cards_options.sort(key=lambda x: misc.simplify(x[1]))
            if cards_options:
                carddef_categories = CardDefCategory.select()
                CardDefCategory.sort_by_position(carddef_categories)
                if carddef_categories:
                    carddef_categories.append(CardDefCategory(pgettext('categories', 'Uncategorised')))
                    for carddef_category in carddef_categories:
                        carddef_category.cards_options = [
                            x for x in cards_options if x[3]['carddef'].category_id == carddef_category.id
                        ]
                        if carddef_category.cards_options:
                            options.append(OptGroup('%s - %s' % (_('Cards'), carddef_category.name)))
                            options.extend(carddef_category.cards_options)
                else:
                    options.append(OptGroup(_('Cards')))
                    options.extend(cards_options)

        if 'named' in allowed_source_types:
            admin_accessible = NamedDataSource.is_admin_accessible()
            nds_options = []
            nds_agenda_options = []
            nds_users_options = []
            for ds in NamedDataSource.select():
                option = [
                    ds.slug,
                    ds.name,
                    ds.slug,
                    {
                        'data-type': ds.type,
                        'data-maybe-datetimes': 'true' if ds.maybe_datetimes() else 'false',
                    },
                ]
                if admin_accessible:
                    option[-1]['data-goto-url'] = ds.get_admin_url()

                if allowed_external_type and ds.external_type != allowed_external_type:
                    continue

                if ds.external == 'agenda':
                    nds_agenda_options.append(option)
                elif ds.type == 'wcs:users':
                    nds_users_options.append(option)
                else:
                    option.append(ds.category)
                    nds_options.append(option)

            nds_agenda_options.sort(key=lambda x: misc.simplify(x[1]))
            if nds_agenda_options:
                options.append(OptGroup(_('Agendas')))
                options.extend(nds_agenda_options)

            nds_users_options.sort(key=lambda x: misc.simplify(x[1]))
            if nds_users_options:
                options.append(OptGroup(_('Users')))
                options.extend(nds_users_options)

            nds_options.sort(key=lambda x: misc.simplify(x[1]))
            if nds_options:
                nds_by_category_names = collections.defaultdict(list)
                for nds in nds_options:
                    name = ''
                    if nds[-1]:
                        name = nds[-1].name
                    nds_by_category_names[name].append(nds[:-1])
                category_names = list(nds_by_category_names.keys())
                if len(category_names) == 1 and category_names[0] == '':
                    # no category found
                    options.append(OptGroup(_('Manually Configured Data Sources')))
                    options.extend(nds_options)
                else:
                    # sort categories
                    category_names = sorted(category_names)
                    # datasources without categories at the end
                    if category_names[0] == '':
                        category_names = category_names[1:] + ['']
                    # group by category name
                    for name in category_names:
                        options.append(OptGroup(name or _('Without category')))
                        options.extend(nds_by_category_names[name])

        generic_options = []
        if 'json' in allowed_source_types:
            generic_options.append(('json', _('JSON URL'), 'json', {'data-maybe-datetimes': 'true'}))
        if 'jsonp' in allowed_source_types:
            generic_options.append(('jsonp', _('JSONP URL'), 'jsonp'))
        elif value.get('type') == 'jsonp':
            generic_options.append(('jsonp', _('JSONP URL (deprecated)'), 'jsonp'))
        if 'geojson' in allowed_source_types:
            generic_options.append(('geojson', _('GeoJSON URL'), 'geojson'))
        if 'jsonvalue' in allowed_source_types:
            generic_options.append(('jsonvalue', _('JSON Expression'), 'jsonvalue'))

        if len(options) > 1 and generic_options:
            options.append(OptGroup(_('Generic Data Sources')))
        options.extend(generic_options)

        self.add(
            SingleSelectWidget,
            'type',
            options=options,
            value=value.get('type'),
            attrs={'data-dynamic-display-parent': 'true'},
        )
        if len(options) > 50:
            widget = self.get_widget('type')
            widget.attrs['data-autocomplete'] = 'true'
            get_response().add_javascript(['select2.js'])

        self.parse()
        if not self.value:
            self.value = {}

        self.add(
            StringWidget,
            'value',
            value=value.get('value'),
            size=80,
            attrs={
                'data-dynamic-display-child-of': 'data_source$type',
                'data-dynamic-display-value-in': 'json|jsonp|geojson|jsonvalue',
            },
        )

        self._parsed = False
        self.initial_value = value

    def _parse(self, request):
        values = {}
        for name in ('type', 'value'):
            value = self.get(name)
            if value:
                values[name] = value

        if values.get('type') in ('json', 'jsonp', 'geojson'):
            url = values.get('value') or ''
            if url:
                if Template.is_template_string(url):
                    try:
                        ComputedExpressionWidget.validate_template(url)
                    except ValidationError as e:
                        self.error = str(e)
                else:
                    parsed = urllib.parse.urlparse(url)
                    if not (parsed.scheme and parsed.netloc):
                        self.error = _('Value must be a full URL.')

        if values.get('type', '') in ('none', ''):
            values = None
        self.value = values or None

    def render_content(self):
        r = TemplateIO(html=True)
        for widget in self.get_widgets():
            r += widget.render_content()
        return r.getvalue()


def get_cache_key(url, data_source):
    cache_key = f'{url}:{data_source.get("storage_timestamp") or 0 if data_source else 0}'
    return force_str(hashlib.md5(force_bytes(cache_key)).hexdigest())


def get_tupled_items(structured_items):
    tupled_items = []
    for item in structured_items:
        tupled_items.append((str(item['id']), str(item['text']), str(item.get('key', item['id'])), item))
    return tupled_items


def get_items(data_source, include_disabled=False, mode=None, metadata=None):
    items = get_structured_items(data_source, mode=mode, include_disabled=include_disabled, metadata=metadata)
    return get_tupled_items(items)


def get_carddef_items(data_source):
    structured_items = get_structured_carddef_items(data_source, with_files_urls=True)
    return get_tupled_items(structured_items)


def get_id_by_option_text(data_source, text_value):
    data_source = get_object(data_source)
    if data_source:
        text_value = str(text_value)
        if data_source.data_source.get('type') == 'json' and data_source.query_parameter:
            url = data_source.get_json_query_url()
            url += urllib.parse.quote(text_value)
            items = request_json_items(url, data_source.extended_data_source)
        elif data_source.data_source.get('type', '').startswith('carddef:'):
            items = data_source.get_card_structured_values_by_text(text_value)
        else:
            items = get_structured_items(data_source.extended_data_source, include_disabled=False)

        # fallback to iterating on all options
        for option in items or []:
            # get raw value from display value
            if option['text'] == text_value:
                return str(option['id'])


def get_json_from_url(
    url, data_source=None, log_message_part='JSON data source', raise_request_error=False, cache_duration=0
):
    add_timing_mark(f'get_json_from_url {url}', url=url)
    if cache_duration:
        cache_key = 'data-source-cache-%s' % get_cache_key(url, data_source)
        entries = cache.get(cache_key)
        if entries is not None:
            return entries

    signed_url = sign_url_auto_orig(url)
    data_source = data_source or {}
    data_key = data_source.get('data_attribute') or 'data'
    geojson = data_source.get('type') == 'geojson'
    error_summary = None

    try:
        entries = json.loads(misc.urlopen(signed_url, error_url=url).read())
        if not isinstance(entries, dict):
            raise ValueError('not a json dict')
        if entries.get('err') not in (None, 0, '0'):
            details = []
            for key in ['err_desc', 'err_class']:
                if entries.get(key):
                    details.append('%s %s' % (key, entries[key]))
            if not details or entries['err'] not in [1, '1']:
                details.append('err %s' % entries['err'])
            raise ValueError(', '.join(details))
        if geojson:
            if not isinstance(entries.get('features'), list):
                raise ValueError('bad geojson format')
        else:
            # data_key can be "data.foo.bar.results"
            keys = data_key.split('.')
            data = entries
            for key in keys[:-1]:
                if not isinstance(data.get(key), dict):
                    raise ValueError('not a json dict with a %s list attribute' % data_key)
                data = data[key]
            if not isinstance(data.get(keys[-1]), list):
                raise ValueError('not a json dict with a %s list attribute' % data_key)
        if cache_duration:
            cache.set(cache_key, entries, cache_duration)
        return entries
    except misc.ConnectionError as e:
        error_summary = 'Error loading %s (%s)' % (log_message_part, str(e))
    except (ValueError, TypeError) as e:
        error_summary = 'Error reading %s output (%s)' % (log_message_part, str(e))

    if data_source:
        get_publisher().record_error(
            error_summary,
            context=_('Data source'),
            notify=data_source.get('notify_on_errors'),
            record=data_source.get('record_on_errors'),
        )

    if raise_request_error:
        raise RequestError(_('Error retrieving data (%s).') % error_summary)
    return None


def request_json_items(url, data_source, cache_duration=0):
    entries = get_json_from_url(url, data_source, cache_duration=cache_duration)
    return extract_json_items(entries, data_source)


def extract_json_items(entries, data_source):
    if entries is None:
        return None
    data_key = data_source.get('data_attribute') or 'data'
    id_attribute = data_source.get('id_attribute') or 'id'
    text_attribute = data_source.get('text_attribute') or 'text'
    # data_key can be "data.foo.bar.results"
    keys = data_key.split('.')
    for key in keys:
        entries = entries[key]

    def get_sub_attribute(item, attribute):
        for key in attribute.split('.'):
            item = item.get(key)
            if item is None:
                return None
        return item

    items = []
    for item in entries:
        # skip malformed items
        if not isinstance(item, dict):
            continue
        item['id'] = get_sub_attribute(item, id_attribute)
        if item['id'] in (None, ''):
            continue
        item['text'] = get_sub_attribute(item, text_attribute)
        if item['text'] is None:
            item['text'] = str(item['id'])
        elif not isinstance(item['text'], str):
            continue
        items.append(item)
    return items


def request_geojson_items(url, data_source, cache_duration=0):
    entries = get_json_from_url(url, data_source, cache_duration=cache_duration)
    if entries is None:
        return None
    items = []
    id_property = data_source.get('id_property') or 'id'
    for item in entries.get('features'):
        if id_property == 'id' and 'id' in item:
            # If a Feature has a commonly used identifier, that identifier
            # SHOULD be included as a member of the Feature object with the
            # name "id", and the value of this member is either a JSON string
            # or number.
            # -- https://tools.ietf.org/html/rfc7946#section-3.2
            pass
        elif item.get('properties', {}).get(id_property):
            item['id'] = item['properties'][id_property]
        else:
            # missing id property, skip entry
            continue
        try:
            item['text'] = Template(
                data_source.get('label_template_property') or '{{ text }}', autoescape=False
            ).render(item['properties'])
        except (TemplateSyntaxError, VariableDoesNotExist):
            pass
        if not item.get('text'):
            item['text'] = item['id']
        items.append(item)
    return items


def get_structured_items(
    data_source,
    mode=None,
    include_disabled=True,
    raise_on_error=False,
    with_file_urls=False,
    selected_ids=None,
    metadata=None,
):
    items = _get_structured_items(
        data_source,
        mode=mode,
        raise_on_error=raise_on_error,
        with_file_urls=with_file_urls,
        selected_ids=selected_ids,
        metadata=metadata,
    )
    if not include_disabled:
        items = [i for i in items if not i.get('disabled')]
    if getattr(get_request(), 'backoffice_form_preview', None):
        # cut-off so the form preview is never exceedingly long
        # (the cards code path will make sure there are no more than 100 items,
        # this part is generic for other data source types)
        items = items[:100]

    if selected_ids is not None:
        # limit to some identifiers (for carddef source the list will already be filtered above)
        str_selected_ids = [str(x) for x in selected_ids]
        items = [x for x in items if str(x.get('id')) in str_selected_ids]

    return items


def get_structured_carddef_items(data_source, with_files_urls=False, selected_ids=None):
    from wcs.carddef import CardDef

    kwargs = {
        'with_files_urls': with_files_urls,
        'structured': True,
        'get_by_ids': selected_ids,
    }
    if getattr(get_request(), 'backoffice_form_preview', None):
        # cut-off so the form preview is never exceedingly long
        kwargs['limit'] = 100

    return CardDef.get_data_source_items(data_source['type'], **kwargs)


def _get_structured_items(
    data_source, mode=None, raise_on_error=False, with_file_urls=False, selected_ids=None, metadata=None
):
    if data_source.get('type') and data_source.get('type').startswith('carddef:'):
        # cards
        return get_structured_carddef_items(
            data_source, with_files_urls=with_file_urls, selected_ids=selected_ids
        )

    cache_duration = 0
    if data_source.get('type') not in ('json', 'jsonp', 'geojson', 'jsonvalue', 'wcs:users'):
        # named data source
        named_data_source = NamedDataSource.get_by_slug(data_source['type'], stub_fallback=True)
        if named_data_source.cache_duration:
            cache_duration = int(named_data_source.cache_duration)
        data_source = named_data_source.extended_data_source

    if data_source.get('type') == 'wcs:users':
        users = get_publisher().user_class.get_users_with_roles(
            included_roles=data_source.get('included_roles'),
            excluded_roles=data_source.get('excluded_roles'),
            include_disabled_users=data_source.get('include_disabled_users'),
            order_by='name',
        )
        role_ids = list(set(itertools.chain.from_iterable(u.get_roles() for u in users)))
        users_roles = get_publisher().role_class.get_ids(ids=role_ids, ignore_errors=True)
        role_prefetch = {str(role.id): role for role in users_roles}

        return [get_data_source_entry_from_user(u, role_prefetch=role_prefetch) for u in users]

    if data_source.get('type') == 'jsonvalue':
        value = data_source.get('value')
        if value is None:
            get_publisher().record_error(
                'JSON data source (%r) gave a non usable result' % data_source.get('value'),
                context=_('Data source'),
                notify=data_source.get('notify_on_errors'),
                record=data_source.get('record_on_errors'),
            )
            return []
        variables = get_publisher().substitutions.get_context_variables(mode=mode)
        try:
            value = Template(value, raises=True).render(context=variables)
        except (TemplateError, TemplateSyntaxError):
            get_publisher().record_error(
                'JSON data source (%r) gave a template syntax error' % data_source.get('value'),
                context=_('Data source'),
                notify=data_source.get('notify_on_errors'),
                record=data_source.get('record_on_errors'),
            )
            return []
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            get_publisher().record_error(
                'JSON data source (%r) gave a non usable result' % data_source.get('value'),
                context=_('Data source'),
                notify=data_source.get('notify_on_errors'),
                record=data_source.get('record_on_errors'),
            )
            return []
        try:
            if not isinstance(value, list):
                raise ValueError
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError
            if all(str(x.get('id', '')) and x.get('text') for x in value):
                return value
            raise ValueError
        except ValueError:
            get_publisher().record_error(
                'JSON data source (%r) gave a non usable result' % data_source.get('value'),
                context=_('Data source'),
                notify=data_source.get('notify_on_errors'),
                record=data_source.get('record_on_errors'),
            )
            return []
    elif data_source.get('type') in ['json', 'geojson']:
        # the content available at a json URL, it must answer with a dict with
        # a 'data' key holding the list of items, each of them being a dict
        # with at least both an "id" and a "text" key.
        geojson = data_source.get('type') == 'geojson'
        url = get_json_url(data_source)
        if not url:
            return []

        request = get_request()
        cache_key = get_cache_key(url, data_source)
        if hasattr(request, 'datasources_cache') and cache_key in request.datasources_cache:
            items, meta = request.datasources_cache[cache_key]
            if metadata is not None:
                metadata.update(meta)
            return items

        if geojson:
            items = request_geojson_items(url, data_source, cache_duration=cache_duration)
        else:
            entries = get_json_from_url(url, data_source, cache_duration=cache_duration)
            if entries and metadata is not None:
                metadata.update(entries.get('meta') or {})

            items = extract_json_items(entries, data_source)

        if items is None:
            if raise_on_error:
                raise DataSourceError('datasource %s is unavailable' % url)
            return []
        if hasattr(request, 'datasources_cache'):
            request.datasources_cache[cache_key] = (items, metadata)

        return items
    return []


def get_json_url(data_source):
    url = data_source.get('value')
    if not url:
        return None
    url = url.strip()
    if Template.is_template_string(url):
        vars = get_publisher().substitutions.get_context_variables(mode='lazy')
        url = get_variadic_url(url, vars)
    if data_source.get('qs_data'):  # merge qs_data into url
        from wcs.workflows import WorkflowStatusItem

        parsed = urllib.parse.urlparse(url)
        qs = list(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in data_source['qs_data'].items():
            try:
                value = WorkflowStatusItem.compute(value, raises=True, record_errors=False)
                value = str(value) if value is not None else ''
            except Exception as e:
                get_publisher().record_error(
                    _(
                        'Failed to compute value "%(value)s" for "%(query)s" query parameter'
                        % {'value': value, 'query': key}
                    ),
                    context=_('Data source'),
                    exception=e,
                    notify=data_source.get('notify_on_errors'),
                    record=data_source.get('record_on_errors'),
                )
            else:
                key = force_str(key)
                value = force_str(value)
                qs.append((key, value))
        qs = urllib.parse.urlencode(qs)
        url = urllib.parse.urlunparse(parsed[:4] + (qs,) + parsed[5:6])
    return url


def get_real(data_source):
    if not data_source:
        return None
    ds_type = data_source.get('type')
    if ds_type in ('json', 'jsonp', 'geojson', 'jsonvalue'):
        return data_source
    if ds_type and ds_type.startswith('carddef:'):
        return data_source
    return NamedDataSource.get_by_slug(ds_type, stub_fallback=True).data_source


def get_object(data_source, ignore_errors=True):
    if not data_source:
        return None
    ds_type = data_source.get('type')
    if ds_type is None:
        return None
    if ds_type in ('json', 'jsonp', 'geojson', 'jsonvalue'):
        named_data_source = NamedDataSource()
        named_data_source.data_source = data_source
        return named_data_source
    if ds_type.startswith('carddef:'):
        named_data_source = NamedDataSource()
        named_data_source.data_source = data_source
        return named_data_source
    return NamedDataSource.get_by_slug(ds_type, ignore_errors=ignore_errors, stub_fallback=True)


class NamedDataSource(wcs.sql.SqlDataSource, StoredObjectMixin, XmlObjectMixin):
    _names = 'datasources'
    xml_root_node = 'datasource'
    backoffice_class = 'wcs.admin.data_sources.NamedDataSourcePage'
    category_class = 'wcs.categories.DataSourceCategory'
    verbose_name = _('Data source')
    verbose_name_plural = _('Data sources')

    id = None
    name = None
    slug = None
    documentation = None
    data_source = None
    cache_duration = None
    query_parameter = None
    id_parameter = None
    data_attribute = None
    id_attribute = None
    text_attribute = None
    id_property = None
    qs_data = None
    label_template_property = None
    external = None
    external_type = None
    external_status = None
    notify_on_errors = False
    record_on_errors = False
    users_included_roles = None
    users_excluded_roles = None
    category_id = None
    include_disabled_users = False
    last_update_time = None

    SLUG_DASH = '_'

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('slug', 'str'),
        ('description', 'str'),  # legacy
        ('documentation', 'str'),
        ('cache_duration', 'str'),
        ('query_parameter', 'str'),
        ('id_parameter', 'str'),
        ('data_attribute', 'str'),
        ('id_attribute', 'str'),
        ('text_attribute', 'str'),
        ('id_property', 'str'),
        ('qs_data', 'qs_data'),
        ('label_template_property', 'str'),
        ('external', 'str'),
        ('external_type', 'str'),
        ('external_status', 'str'),
        ('data_source', 'data_source'),
        ('notify_on_errors', 'bool'),
        ('record_on_errors', 'bool'),
        ('users_included_roles', 'ds_roles'),
        ('users_excluded_roles', 'ds_roles'),
        ('include_disabled_users', 'bool'),
    ]

    def __init__(self, name=None):
        self.name = name

    def migrate(self):
        from wcs.data_sources_agendas import has_chrono, translate_url

        changed = False

        # 2023-05-30
        publisher = get_publisher()
        if self.agenda_ds and has_chrono(publisher):
            url = (self.data_source or {}).get('value')
            if url and not url.startswith('{{'):
                self.data_source['value'] = translate_url(publisher, url)
                changed = True

        if getattr(self, 'description', None):  # 2024-04-07
            self.documentation = getattr(self, 'description')
            self.description = None
            changed = True

        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)

    @property
    def category(self):
        from wcs.categories import DataSourceCategory

        return DataSourceCategory.get(self.category_id, ignore_errors=True)

    @category.setter
    def category(self, category):
        if category:
            self.category_id = category.id
        elif self.category_id:
            self.category_id = None

    @property
    def type(self):
        if not self.data_source:
            return None
        return self.data_source.get('type')

    @property
    def extended_data_source(self):
        notify_on_errors = self.notify_on_errors
        record_on_errors = self.record_on_errors
        if getattr(get_request(), 'disable_error_notifications', None) is True:
            notify_on_errors = False
            record_on_errors = False
        if self.type == 'geojson':
            data_source = self.data_source.copy()
            data_source.update(
                {
                    'id_property': self.id_property,
                    'label_template_property': self.label_template_property,
                    'notify_on_errors': notify_on_errors,
                    'record_on_errors': record_on_errors,
                    'storage_timestamp': str(self.last_update_time or 0),
                }
            )
            return data_source
        if self.type == 'json':
            data_source = self.data_source.copy()
            data_source.update(
                {
                    'data_attribute': self.data_attribute,
                    'id_attribute': self.id_attribute,
                    'text_attribute': self.text_attribute,
                    'qs_data': self.qs_data,
                    'notify_on_errors': notify_on_errors,
                    'record_on_errors': record_on_errors,
                    'storage_timestamp': str(self.last_update_time or 0),
                }
            )
            return data_source
        if self.type == 'wcs:users':
            data_source = self.data_source.copy()
            data_source.update(
                {
                    'included_roles': self.users_included_roles,
                    'excluded_roles': self.users_excluded_roles,
                    'include_disabled_users': self.include_disabled_users,
                }
            )
            return data_source
        return self.data_source

    def can_geojson(self):
        return bool(self.type == 'geojson')

    def can_jsonp(self):
        if self.type == 'jsonp':
            return True
        if self.type == 'json' and self.query_parameter:
            return True
        if self.type and self.type.startswith('carddef:'):
            return True
        return False

    def maybe_datetimes(self):
        if self.type == 'json':
            if 'datetimes' in (self.data_source.get('value') or ''):
                return True
            if not self.id and Template.is_template_string(self.data_source.get('value') or ''):
                # unsaved datasource is used when checking display mode; we allow any template
                # in that case.
                return True
        return False

    def can_images(self):
        if self.type and self.type.startswith('carddef:'):
            from wcs.carddef import CardDef

            carddef = CardDef.get_by_slug(self.type.split(':')[1])
            return carddef.has_image_field()
        return False

    @property
    def agenda_ds(self):
        return self.external in ['agenda', 'agenda_manual']

    @property
    def agenda_ds_origin(self):
        if self.external != 'agenda_manual':
            return
        for datasource in NamedDataSource.select():
            if datasource.external != 'agenda':
                continue
            if datasource.data_source.get('value') == self.data_source.get('value'):
                return datasource

    def store(self, comment=None, snapshot_store_user=True, application=None, *args, **kwargs):
        assert not self.is_readonly()
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()
        super().store(*args, **kwargs)
        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self, comment=comment, store_user=snapshot_store_user, application=application
            )

    @classmethod
    def is_admin_accessible(cls):
        for section in ('settings', 'forms', 'workflows'):
            if get_publisher().get_backoffice_root().is_accessible(section):
                return True
        return False

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        if get_request():
            for section in ('settings', 'forms', 'workflows'):
                bo_root = get_publisher().get_backoffice_root()
                if bo_root and bo_root.is_accessible(section):
                    return '%s/%s/data-sources/%s/' % (base_url, section, self.id)
        # fallback to settings section
        section = 'settings'
        return '%s/%s/data-sources/%s/' % (base_url, section, self.id)

    def export_data_source_to_xml(self, element, attribute_name, **kwargs):
        data_source = getattr(self, attribute_name)
        ET.SubElement(element, 'type').text = data_source.get('type')
        ET.SubElement(element, 'value').text = data_source.get('value') or ''

    def import_data_source_from_xml(self, element, **kwargs):
        return {
            'type': force_str(element.find('type').text),
            'value': force_str(element.find('value').text or ''),
        }

    def export_qs_data_to_xml(self, element, attribute_name, *args, **kwargs):
        if not self.qs_data:
            return
        for key, value in self.qs_data.items():
            item = ET.SubElement(element, 'item')
            if isinstance(key, str):
                ET.SubElement(item, 'name').text = force_str(key)
            else:
                raise AssertionError('unknown type for key (%r)' % key)
            if isinstance(value, str):
                ET.SubElement(item, 'value').text = force_str(value)
            else:
                raise AssertionError('unknown type for value (%r)' % key)

    def import_qs_data_from_xml(self, element, **kwargs):
        if element is None:
            return
        qs_data = {}
        for item in element.findall('item'):
            key = force_str(item.find('name').text)
            value = force_str(item.find('value').text or '')
            qs_data[key] = value
        return qs_data

    def get_dependencies(self):
        if self.category_id:
            yield self.category
        if self.type == 'wcs:users':
            role_ids = list(
                set(itertools.chain(self.users_included_roles or [], self.users_excluded_roles or []))
            )
            yield from get_publisher().role_class.get_ids(ids=role_ids, ignore_errors=True)

        if self.data_source:
            yield from misc.get_dependencies_from_template(self.data_source.get('value'))

        for value in (self.qs_data or {}).values():
            yield from misc.get_dependencies_from_template(value)

    def export_to_xml(self, include_id=False):
        from wcs.categories import DataSourceCategory

        root = super().export_to_xml(include_id=include_id)
        DataSourceCategory.object_category_xml_export(self, root, include_id=include_id)
        return root

    @classmethod
    def import_from_xml_tree(cls, tree, include_id=False, check_deprecated=False, **kwargs):
        from wcs.backoffice.deprecations import DeprecatedElementsDetected, DeprecationsScan
        from wcs.categories import DataSourceCategory

        data_source = super().import_from_xml_tree(
            tree, include_id=include_id, check_deprecated=check_deprecated, **kwargs
        )
        DataSourceCategory.object_category_xml_import(data_source, tree, include_id=include_id)

        if check_deprecated:
            # check for deprecated elements
            job = DeprecationsScan()
            try:
                job.check_deprecated_elements_in_object(data_source)
            except DeprecatedElementsDetected as e:
                raise NamedDataSourceImportError(str(e))

        return data_source

    @classmethod
    def get_by_slug(cls, slug, ignore_errors=True, stub_fallback=False):
        data_source = super().get_by_slug(slug, ignore_errors=ignore_errors)
        if data_source is None:
            if stub_fallback:
                get_logger().warning("data source '%s' does not exist" % slug)
                return StubNamedDataSource(name=slug)
        return data_source

    def get_json_query_url(self):
        url = self.get_variadic_url()
        if not url:
            return ''
        if '?' not in url:
            url += '?' + self.query_parameter + '='
        else:
            url += '&' + self.query_parameter + '='
        return url

    def get_jsonp_url(self, **kwargs):
        if self.type == 'jsonp':
            return self.data_source.get('value')

        token_context = {}
        if self.type == 'json' and self.query_parameter:
            json_url = self.get_json_query_url()
            token_context = {'url': json_url, 'data_source': self.id}

        elif self.type and self.type.startswith('carddef:'):
            token_context = {'carddef_ref': self.type, **kwargs}

            if get_request().edited_test_id:
                token_context['edited_test_id'] = get_request().edited_test_id

            parts = self.type.split(':')
            if len(parts) > 2:
                # custom view, check if it's dynamic
                from wcs.carddef import CardDef
                from wcs.workflows import WorkflowStatusItem

                custom_view = CardDef.get_data_source_custom_view(self.type)
                if custom_view is None:
                    get_publisher().record_error(
                        _('Unknown custom view "%(view)s" for CardDef "%(card)s"')
                        % {'view': parts[2], 'card': parts[1]},
                        context=_('Data source'),
                        notify=True,
                        record=True,
                    )
                else:
                    had_template = False
                    for filter_key, filter_value in custom_view.filters.items():
                        if not Template.is_template_string(filter_value):
                            continue
                        custom_view.filters[filter_key] = WorkflowStatusItem.compute(filter_value)
                        had_template = True
                    if had_template:
                        # keep altered custom view in token
                        token_context.update(
                            {
                                'dynamic_custom_view': custom_view.id,
                                'dynamic_custom_view_filters': custom_view.filters,
                            }
                        )

        if token_context:
            token = get_session().create_token('autocomplete', token_context)
            return '/api/autocomplete/%s' % token.id

        return None

    def get_geojson_url(self):
        assert self.type == 'geojson'
        url = self.data_source.get('value').strip()
        new_url = self.get_variadic_url()
        if new_url != url:
            token_context = {'url': new_url, 'slug': self.slug}
            token = get_session().create_token('geojson', token_context)
            return '/api/geojson/%s' % token.id
        return '/api/geojson/%s' % self.slug

    def get_geojson_data(self, force_url=None):
        if force_url:
            url = force_url
        else:
            url = self.get_variadic_url()

        request = get_request()
        cache_key = get_cache_key(url, self.extended_data_source)
        if hasattr(request, 'datasources_cache') and cache_key in request.datasources_cache:
            return request.datasources_cache[cache_key]

        cache_duration = 0
        if self.cache_duration:
            cache_duration = int(self.cache_duration)

        data = get_json_from_url(
            url, self.data_source, raise_request_error=True, cache_duration=cache_duration
        )
        id_property = self.id_property or 'id'
        label_template_property = self.label_template_property or '{{ text }}'

        features = []
        for feature in data['features']:
            if id_property not in feature['properties']:
                # missing id property, skip entry
                continue
            feature['properties']['_id'] = feature['properties'][id_property]
            try:
                feature['properties']['_text'] = Template(label_template_property, autoescape=False).render(
                    feature['properties']
                )
            except (TemplateSyntaxError, VariableDoesNotExist):
                pass
            if not feature['properties'].get('_text'):
                feature['properties']['_text'] = feature['properties']['_id']
            features.append(feature)
        data['features'] = features

        if hasattr(request, 'datasources_cache'):
            request.datasources_cache[cache_key] = data

        return data

    def get_value_by_id(self, param_name, param_value):
        url = self.get_variadic_url()

        if param_value is None:
            return None

        param_value = str(param_value)

        if '?' not in url:
            url += '?'
        else:
            url += '&'
        url += param_name + '=' + urllib.parse.quote(param_value)

        def find_item(items, name, value):
            for item in items:
                if str(item.get(name)) == str(value):
                    return item
            # not found
            get_publisher().record_error(_('Could not find element by id "%s"') % value)
            return None

        request = get_request()
        if hasattr(request, 'datasources_cache') and url in request.datasources_cache:
            items = request.datasources_cache[url]
            if not items:  # cache may contains empty list from get_structured_items
                return None
            return find_item(items, param_name, param_value)

        items = request_json_items(url, self.extended_data_source)
        if not items:  # None or empty list are not valid
            return None
        if hasattr(request, 'datasources_cache'):
            request.datasources_cache[url] = items
        return find_item(items, param_name, param_value)

    def get_card_structured_value_by_id(self, option_id):
        from wcs.carddef import CardDef

        if option_id is None:
            return None

        values = CardDef.get_data_source_items(self.type, get_by_id=option_id)
        if not values:
            values = CardDef.get_data_source_items(self.type, get_by_text=str(option_id))
            if not values:
                return None
        return values[0]

    def get_card_structured_values_by_text(self, option_text):
        from wcs.carddef import CardDef

        if option_text is None:
            return []

        return CardDef.get_data_source_items(self.type, get_by_text=option_text)

    def get_display_value(self, option_id):
        value = self.get_structured_value(option_id)
        if value:
            return value.get('text')
        return None

    def get_structured_value(self, option_id, check_value_type=False):
        value = None

        if self.type == 'wcs:users' and isinstance(option_id, get_publisher().user_class):
            option_id = option_id.id

        if check_value_type and not isinstance(option_id, (int, str)):
            get_publisher().record_error(_('Invalid type for item lookup (%r)') % option_id)
            return None

        if self.type and self.type.startswith('carddef:'):
            value = self.get_card_structured_value_by_id(option_id)
        elif self.type == 'json' and self.id_parameter:
            value = self.get_value_by_id(self.id_parameter, option_id)
        elif self.type == 'wcs:users':
            value = get_publisher().user_class.get_user_with_roles(
                option_id,
                included_roles=self.users_included_roles,
                excluded_roles=self.users_excluded_roles,
                include_disabled_users=self.include_disabled_users,
                order_by='name',
            )
            if value:
                value = get_data_source_entry_from_user(value)

        else:
            structured_items = get_structured_items(self.extended_data_source, mode='lazy')
            for item in structured_items:
                if str(item['id']) == str(option_id):
                    value = item
                    break
            else:
                # recheck in case option label was given instead of option id.
                for item in structured_items:
                    if str(item['text']) == str(option_id):
                        value = item
                        break
        if value is None:
            return None
        return value

    @classmethod
    def get_substitution_variables(cls):
        return {'data_source': DataSourcesSubstitutionProxy()}

    def type_label(self):
        data_source_labels = {
            'wcs:users': _('Users'),
            'json': _('JSON'),
            'jsonp': _('JSONP'),
            'geojson': _('GeoJSON'),
            'jsonvalue': _('JSON Expression'),
        }
        data_source_type = self.data_source.get('type')
        return data_source_labels.get(data_source_type)

    def humanized_cache_duration(self):
        return seconds2humanduration(int(self.cache_duration))

    def get_referenced_varnames(self, formdef):
        from .fields import Field

        if self.type in ('json', 'jsonvalue', 'geojson'):
            values = [self.data_source.get('value')]
            values.extend((self.qs_data or {}).values())

            varnames = []
            for value in values:
                varnames.extend(Field.get_referenced_varnames(formdef, value))

            return varnames

        # else: carddef
        assert self.type.startswith('carddef:'), 'data source must be carddef'
        from wcs.carddef import CardDef

        return CardDef.get_data_source_referenced_varnames(self.type, formdef=formdef)

    def get_variadic_url(self):
        url = self.data_source.get('value').strip()
        if url and Template.is_template_string(url):
            vars = get_publisher().substitutions.get_context_variables(mode='lazy')
            url = get_variadic_url(url, vars)
        return url

    def is_used(self):
        from wcs.formdef_base import get_formdefs_of_all_kinds

        for formdef in get_formdefs_of_all_kinds():
            if any(self.usage_in_formdef(formdef)):
                return True
        return False

    def usage_in_formdef(self, formdef):
        for field in formdef.fields or []:
            data_source = getattr(field, 'data_source', None)
            if not data_source:
                continue
            if data_source.get('type') == self.slug:
                field._formdef = formdef
                yield field


class StubNamedDataSource(NamedDataSource):
    type = 'jsonvalue'
    data_source = {'type': 'jsonvalue', 'value': '[]'}
    cache_duration = None

    def __init__(self, name=None):
        self.name = name

    def store(self):
        pass

    def get_admin_url(self):
        return '#invalid-%s' % self.name

    def __repr__(self):
        return '<StubNamedDataSource %r>' % self.name


class DataSourcesSubstitutionProxy:
    def __getattr__(self, attr):
        if attr == 'inspect_collapse':
            return True
        return DataSourceProxy(attr)

    def inspect_keys(self):
        return []


class DataSourceProxy:
    def __init__(self, name):
        self.name = name
        self.data_source = NamedDataSource.get_by_slug(self.name, stub_fallback=True)
        self._list = get_structured_items(self.data_source.extended_data_source)
        self._data = Ellipsis

    def get_value(self):
        return self._list

    def __len__(self):
        return len(self._list)

    def __str__(self):
        return str(self._list)

    def __repr__(self):
        return '<DataSourceProxy, %s>' % self.name

    def __iter__(self):
        yield from self._list

    def __nonzero__(self):
        return any(self)

    def __contains__(self, value):
        return value in list(self)

    def __eq__(self, other):
        return list(self) == list(other)

    def __getitem__(self, key):
        return list(self)[key]

    def __getattr__(self, attr):
        data_source = self.data_source.extended_data_source
        if data_source.get('type') not in ['json', 'geojson']:
            raise AttributeError
        if self._data is Ellipsis:
            url = get_json_url(data_source)
            self._data = get_json_from_url(url, data_source)
        if self._data is None:
            raise AttributeError
        try:
            return self._data[attr]
        except KeyError as e:
            raise AttributeError(attr) from e
