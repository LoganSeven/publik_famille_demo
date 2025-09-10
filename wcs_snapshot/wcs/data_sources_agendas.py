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

import urllib.parse

from quixote import get_publisher

from wcs.qommon import _
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.cron import CronJob
from wcs.qommon.publisher import get_publisher_class


def has_chrono(publisher):
    return publisher.get_site_option('chrono_url') is not None


def chrono_url(publisher, url):
    chrono_url = publisher.get_site_option('chrono_url')
    return urllib.parse.urljoin(chrono_url, url)


def chrono_variable(publisher):
    chrono_url = publisher.get_site_option('chrono_url')
    for key, value in publisher.get_site_options(section='variables').items():
        if value == chrono_url:
            return key


def translate_url(publisher, url):
    variable = chrono_variable(publisher)
    if not variable:
        return url
    chrono_url = publisher.get_site_option('chrono_url')
    return url.replace(chrono_url, '{{ %s }}' % variable)


def collect_agenda_data(publisher):
    from wcs.data_sources import get_json_from_url

    agenda_url = chrono_url(publisher, 'api/agenda/')
    result = get_json_from_url(agenda_url, log_message_part='agenda')
    if result is None:
        return

    # build datasources from chrono
    agenda_data = []
    for agenda in result.get('data') or []:
        if agenda['kind'] == 'events':
            agenda_data.append(
                {
                    'slug': 'agenda-%s-%s' % (agenda['kind'], agenda['id']),
                    'text': agenda['text'],
                    'url': agenda['api']['datetimes_url'],
                    'qs_data': {
                        'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'
                    },
                }
            )
        elif agenda['kind'] == 'meetings' and agenda['free_range']:
            agenda_data.append(
                {
                    'slug': 'agenda-partial-bookings-meetings-%s-resources' % (agenda['id']),
                    'text': _('%s - Resources') % agenda['text'],
                    'url': agenda['api']['resources_url'],
                }
            )
            agenda_data.append(
                {
                    'slug': 'agenda-partial-bookings-meetings-%s-resourcedynamic' % (agenda['id']),
                    'text': _('%s - Slots of type form_var_resource_raw') % agenda['text'],
                    'url': agenda['api']['datetimes_url'],
                    'qs_data': {
                        'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}',
                        'resource': '{{ form_var_resource_raw|default:"" }}',
                        'user_external_id': '{{ form_uuid }}',
                    },
                    'free_range': True,
                }
            )
            # get also resources
            resources_url = chrono_url(publisher, 'api/agenda/%s/resources/' % agenda['id'])
            resources_results = get_json_from_url(resources_url, log_message_part='agenda')
            if resources_results is None:
                return
            for resource in resources_results.get('data') or []:
                agenda_data.append(
                    {
                        'slug': 'agenda-partial-bookings-meetings-%s-resource-%s'
                        % (agenda['id'], resource['id']),
                        'text': _('%(agenda)s - Slots for resource %(type)s')
                        % {'agenda': agenda['text'], 'type': resource['text']},
                        'url': agenda['api']['datetimes_url'],
                        'qs_data': {
                            'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}',
                            'resource': resource['id'],
                            'user_external_id': '{{ form_uuid }}',
                        },
                        'free_range': True,
                    }
                )
        elif agenda['kind'] in ['meetings', 'virtual']:
            agenda_data.append(
                {
                    'slug': 'agenda-%s-%s-meetingtypes' % (agenda['kind'], agenda['id']),
                    'text': _('%s - Meeting types') % agenda['text'],
                    'url': agenda['api']['meetings_url'],
                }
            )
            agenda_data.append(
                {
                    'slug': 'agenda-%s-%s-mtdynamic' % (agenda['kind'], agenda['id']),
                    'text': _('%s - Slots of type form_var_meeting_type_raw') % agenda['text'],
                    'url': '%s{{ form_var_meeting_type_raw }}/datetimes/' % agenda['api']['meetings_url'],
                    'qs_data': {
                        'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'
                    },
                }
            )
            # get also meeting types
            mt_url = chrono_url(publisher, 'api/agenda/%s/meetings/' % agenda['id'])
            mt_results = get_json_from_url(mt_url, log_message_part='agenda')
            if mt_results is None:
                return
            for meetingtype in mt_results.get('data') or []:
                agenda_data.append(
                    {
                        'slug': 'agenda-%s-%s-mt-%s' % (agenda['kind'], agenda['id'], meetingtype['id']),
                        'text': _('%(agenda)s - Slots of type %(type)s (%(duration)s minutes)')
                        % {
                            'agenda': agenda['text'],
                            'type': meetingtype['text'],
                            'duration': meetingtype['duration'],
                        },
                        'url': meetingtype['api']['datetimes_url'],
                        'qs_data': {
                            'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'
                        },
                    }
                )
    return agenda_data


def build_agenda_datasources(publisher, **kwargs):
    from wcs.data_sources import NamedDataSource

    if not has_chrono(publisher):
        return

    agenda_data = collect_agenda_data(publisher)
    if agenda_data is None:
        return

    # fetch existing datasources
    existing_datasources = {}
    for datasource in NamedDataSource.select():
        if datasource.external != 'agenda':
            continue
        url = datasource.data_source['value']
        qs_data = datasource.qs_data or {}
        url += '?' + urllib.parse.urlencode(sorted(qs_data.items()))
        existing_datasources[url] = datasource
    seen_datasources = []

    # build datasources from chrono
    for agenda in agenda_data:
        qs_data = agenda.get('qs_data', {})
        base_url = translate_url(publisher, agenda['url'])
        qs_data = agenda.get('qs_data') or {}
        url = base_url + '?' + urllib.parse.urlencode(sorted(qs_data.items()))
        store = False
        datasource = existing_datasources.get(url)
        if datasource is None:
            store = True
            datasource = NamedDataSource()
            datasource.slug = datasource.get_new_slug('chrono_ds_%s' % agenda['slug'])
            datasource.external = 'agenda'
            datasource.data_source = {'type': 'json', 'value': base_url}

            if agenda.get('free_range'):
                datasource.external_type = 'free_range'

        for key, value in [
            ('external_status', None),  # reset
            ('record_on_errors', False),  # those will be internal publik errors
            ('notify_on_errors', True),  # that should be notified to sysadmins.
            ('name', agenda['text']),
            ('qs_data', agenda.get('qs_data')),
        ]:
            if getattr(datasource, key) != value:
                setattr(datasource, key, value)
                store = True
        if store:
            datasource.store()
        # maintain caches
        existing_datasources[url] = datasource
        seen_datasources.append(url)

    # now check outdated agenda datasources
    for url, datasource in existing_datasources.items():
        if url in seen_datasources:
            continue
        if datasource.is_used():
            datasource.external_status = 'not-found'
            datasource.store()
            continue
        datasource.remove_self()


class RefreshAgendas(AfterJob):
    label = _('Refreshing agendas')

    def execute(self):
        build_agenda_datasources(get_publisher())


def register_cronjob():
    # every hour: check for agenda datasources
    get_publisher_class().register_cronjob(
        CronJob(build_agenda_datasources, name='build_agenda_datasources', minutes=[0])
    )
