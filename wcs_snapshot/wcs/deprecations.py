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
import json
import os

from quixote import get_publisher

from wcs.qommon import _

HELP_URL = 'https://doc-publik.entrouvert.com/admin-fonctionnel/elements-deprecies/'

DEPRECATIONS_METADATA = {
    'ezt': {
        'title': _('EZT text'),
        'short_doc': _('Use Django templates.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
        'removal_date': datetime.date(2025, 10, 31),
        'killswitches': [
            'disable-ezt-support',
        ],
    },
    'jsonp': {
        'title': _('JSONP data source'),
        'short_doc': _('Use JSON sources with id and query parameters.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
    },
    'rtf': {
        'title': _('RTF Documents'),
        'short_doc': _('Use OpenDocument format.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 4, 11),
        'removal_date': datetime.date(2024, 12, 31),
    },
    'script': {
        'title': _('Filesystem Script'),
        'short_doc': _('Use a dedicated template tags application.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
        'removal_date': datetime.date(2024, 12, 31),
    },
    'fields': {
        'title': _('Obsolete field types'),
        'short_doc': _('Use block fields to replace tables and ranked order fields.'),
        'help_url': HELP_URL,
    },
    'fargo': {
        'title': _('Portfolio'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
        'removal_date': datetime.date(2025, 12, 31),
    },
    'action-aggregationemail': {
        'title': _('Obsolete action type: Daily Summary Email'),
        'help_url': HELP_URL,
    },
    'action-resubmit': {
        'title': _('Obsolete action type: Resubmission'),
        'help_url': HELP_URL,
    },
    'csv-connector': {
        'title': _('CSV connector'),
        'short_doc': _('Use cards.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
    },
    'json-data-store': {
        'title': _('JSON Data Store connector'),
        'short_doc': _('Use cards.'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2022, 3, 29),
    },
    'field-limits': {
        'title': _('Limits on number of fields'),
        'help_url': HELP_URL,
    },
    'internal-statistics': {
        'title': _('Internal statistics'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2025, 2, 2),
        'removal_date': datetime.date(2025, 9, 3),
        'killswitches': [
            'disable-internal-statistics',
        ],
    },
    'legacy_wf_form_variables': {
        'title': _('Legacy access to workflow form action variables'),
        'help_url': HELP_URL,
        'deprecation_date': datetime.date(2025, 8, 8),
    },
}


def get_report_path():
    return os.path.join(get_publisher().app_dir, 'deprecations.json')


def has_urgent_deprecations():
    report_path = get_report_path()
    if not os.path.exists(report_path):
        return False
    with open(report_path) as fd:
        report = json.load(fd)
    soon = datetime.date.today() + datetime.timedelta(days=90)
    for line in report['report_lines']:
        removal_date = DEPRECATIONS_METADATA.get(line['category'], {}).get('removal_date')
        if removal_date and soon > removal_date:
            return True
    return False
