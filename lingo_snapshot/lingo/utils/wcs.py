# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

import json
import re

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from requests.exceptions import RequestException

from lingo.utils import requests


class WCSError(Exception):
    pass


def is_wcs_enabled():
    return hasattr(settings, 'KNOWN_SERVICES') and settings.KNOWN_SERVICES.get('wcs')


def get_wcs_services():
    if not is_wcs_enabled():
        return {}
    return settings.KNOWN_SERVICES.get('wcs')


def get_default_wcs_service_key():
    services = get_wcs_services()

    for key, service in services.items():
        if not service.get('secondary', False):
            # if secondary is not set or not set to True, return this one
            return key

    return None


def get_wcs_json(wcs_site, path, log_errors=True):
    if wcs_site is None:
        # no site specified (probably an import referencing a not yet deployed
        # site)
        return {'err': 1, 'err_desc': 'no-wcs-site'}
    try:
        response = requests.get(
            path,
            remote_service=wcs_site,
            without_user=True,
            headers={'accept': 'application/json'},
            log_errors=log_errors,
        )
        response.raise_for_status()
    except RequestException as e:
        if e.response is not None:
            try:
                # return json if available (on 404 responses by example)
                return e.response.json()
            except json.JSONDecodeError:
                return {
                    'err': 1,
                    'err_desc': 'request-error-status-%s' % e.response.status_code,
                    'data': None,
                }
        return {'err': 1, 'err_desc': 'request-error', 'data': None}
    return response.json()


def get_wcs_options(url):
    references = []
    for wcs_key, wcs_site in sorted(get_wcs_services().items(), key=lambda x: x[1]['title']):
        site_title = wcs_site.get('title')
        response_json = get_wcs_json(wcs_site, url)
        if response_json.get('err') == 1:
            continue
        response_json = response_json.get('data')
        if not response_json:
            continue
        for element in response_json:
            slug = element.get('slug')
            title = element.get('title')
            if len(get_wcs_services()) == 1:
                label = title
            else:
                label = '%s : %s' % (site_title, title)
            reference = '%s:%s' % (wcs_key, slug)
            references.append((reference, label))
    return references


def get_wcs_matching_card_model(ref):
    card_models = get_wcs_options('/api/cards/@list')
    for carddef_reference, card_label in card_models:
        if carddef_reference == ref:
            return card_label


def get_wcs_dependencies_from_template(string):
    if not is_wcs_enabled():
        return []
    service_key = get_default_wcs_service_key()
    wcs = get_wcs_services().get(service_key)
    wcs_url = wcs.get('url') or ''
    response_json = get_wcs_json(wcs, '/api/cards/@list')
    if response_json.get('err') == 1:
        raise WCSError(_('Unable to get WCS service (%s)') % response_json.get('err_desc'))
    if not response_json.get('data'):
        raise WCSError(_('Unable to get WCS data'))
    carddef_labels_by_slug = {e['slug']: e['title'] for e in response_json['data']}
    for carddef_slug in re.findall(r'cards\|objects:"([\w_-]+:?[\w_-]*)"', string):
        if ':' in carddef_slug:
            carddef_slug = carddef_slug.split(':')[0]
        if carddef_slug not in carddef_labels_by_slug:
            # ignore unknown card model
            continue
        yield {
            'type': 'cards',
            'id': carddef_slug,
            'text': carddef_labels_by_slug[carddef_slug],
            'urls': {
                'export': f'{wcs_url}api/export-import/cards/{carddef_slug}/',
                'dependencies': f'{wcs_url}api/export-import/cards/{carddef_slug}/dependencies/',
                'redirect': f'{wcs_url}api/export-import/cards/{carddef_slug}/redirect/',
            },
        }
