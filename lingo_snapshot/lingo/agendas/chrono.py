# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext
from requests.exceptions import RequestException

from lingo.agendas.models import Agenda
from lingo.utils import requests


class ChronoError(Exception):
    def __init__(self, msg):
        self.msg = msg


def is_chrono_enabled():
    return hasattr(settings, 'KNOWN_SERVICES') and settings.KNOWN_SERVICES.get('chrono')


def get_chrono_service():
    if not is_chrono_enabled():
        return {}
    return list(settings.KNOWN_SERVICES.get('chrono').values())[0]


def chrono_json(path, params=None, json_params=None, log_errors=True, method='get'):
    chrono_site = get_chrono_service()
    if chrono_site is None:
        return
    try:
        response = getattr(requests, method)(
            path,
            params=params or {},
            json=json_params or {},
            remote_service=chrono_site,
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
                pass
        return
    return response.json()


def collect_agenda_data(slug=None):
    result = chrono_json('api/agenda/', params={'q': slug} if slug else {})
    if result is None:
        return
    if result.get('data') is None:
        return

    agenda_data = []
    for agenda in result['data']:
        if agenda['kind'] != 'events':
            continue
        agenda_data.append(
            {
                'slug': agenda['slug'],
                'label': agenda['text'],
                'category_slug': agenda['category'],
                'category_label': agenda['category_label'],
                'partial_bookings': agenda.get('partial_bookings') or False,
            }
        )
    return agenda_data


def refresh_agendas(q_slug=None):
    result = collect_agenda_data(slug=q_slug)
    if result is None:
        return

    # fetch existing agendas
    existing_agendas = {a.slug: a for a in Agenda.objects.all()}
    seen_agendas = []

    # build agendas from chrono
    for agenda_data in result:
        slug = agenda_data['slug']
        agenda_data['archived'] = False
        agenda, created = Agenda.objects.update_or_create(slug=slug, defaults=agenda_data)
        if created:
            agenda.take_snapshot(comment=pgettext('snapshot', 'created'))
        seen_agendas.append(agenda.slug)

    if q_slug:
        # do not remove agendas when not collecting them all
        return

    # now check outdated agendas
    for slug, agenda in existing_agendas.items():
        if slug not in seen_agendas:
            if agenda.campaigns.exists():
                agenda.archived = True
                agenda.save()
                continue
            if agenda.pricings.exists():
                agenda.archived = True
                agenda.save()
                continue
            agenda.delete()


def get_event(event_slug):
    return get_events(
        [event_slug],
        error_message=_('Unable to get event details'),
        error_message_with_details=_('Unable to get event details (%s)'),
    )[0]


def get_events(event_slugs, error_message=None, error_message_with_details=None):
    error_message = error_message or _('Unable to get events details')
    error_message_with_details = error_message_with_details or _('Unable to get events details (%s)')
    result = chrono_json('api/agendas/events/', json_params={'slots': event_slugs}, method='post')
    if not result:
        raise ChronoError(error_message)
    if result.get('err'):
        raise ChronoError(error_message_with_details % result['err_desc'])
    if not result.get('data'):
        raise ChronoError(error_message)
    return result['data']


def get_subscriptions(agenda_slug, user_external_id=None, date_start=None, date_end=None):
    url = 'api/agenda/%s/subscription/' % agenda_slug
    params = {}
    if user_external_id:
        params['user_external_id'] = user_external_id
    if date_start:
        params['date_start'] = date_start
    if date_end:
        params['date_end'] = date_end
    if params:
        url += '?%s' % '&'.join(['%s=%s' % (k, v) for k, v in params.items()])
    result = chrono_json(url)
    if not result:
        raise ChronoError(_('Unable to get subscription details'))
    if result.get('err'):
        raise ChronoError(_('Unable to get subscription details (%s)') % result['err_desc'])
    if 'data' not in result:
        raise ChronoError(_('Unable to get subscription details'))
    return result['data']


def get_check_status(agenda_slugs, user_external_id, date_start, date_end):
    result = chrono_json(
        'api/agendas/events/check-status/',
        json_params={
            'user_external_id': user_external_id,
            'agendas': ','.join(agenda_slugs),
            'date_start': date_start.isoformat(),
            'date_end': date_end.isoformat(),
        },
        method='post',
    )
    if not result:
        raise ChronoError(_('Unable to get check status'))
    if result.get('err'):
        raise ChronoError(_('Unable to get check status (%s)') % result['err_desc'])
    if 'data' not in result:
        raise ChronoError(_('Unable to get check status'))
    return result['data']


def lock_events_check(agenda_slugs, date_start, date_end):
    result = chrono_json(
        '/api/agendas/events/check-lock/',
        json_params={
            'check_locked': True,
            'agendas': ','.join(agenda_slugs),
            'date_start': date_start.isoformat(),
            'date_end': date_end.isoformat(),
        },
        method='post',
    )
    if not result:
        raise ChronoError(_('Unable to lock events check'))
    if result.get('err'):
        raise ChronoError(_('Unable to lock events check (%s)') % result['err_desc'])


def unlock_events_check(agenda_slugs, date_start, date_end):
    result = chrono_json(
        '/api/agendas/events/check-lock/',
        json_params={
            'check_locked': False,
            'agendas': ','.join(agenda_slugs),
            'date_start': date_start.isoformat(),
            'date_end': date_end.isoformat(),
        },
        method='post',
    )
    if not result:
        raise ChronoError(_('Unable to unlock events check'))
    if result.get('err'):
        raise ChronoError(_('Unable to unlock events check (%s)') % result['err_desc'])


def mark_events_invoiced(agenda_slugs, date_start, date_end):
    result = chrono_json(
        '/api/agendas/events/invoiced/',
        json_params={
            'invoiced': True,
            'agendas': ','.join(agenda_slugs),
            'date_start': date_start.isoformat(),
            'date_end': date_end.isoformat(),
        },
        method='post',
    )
    if not result:
        raise ChronoError(_('Unable to mark events as invoiced'))
    if result.get('err'):
        raise ChronoError(_('Unable to mark events as invoiced (%s)') % result['err_desc'])
