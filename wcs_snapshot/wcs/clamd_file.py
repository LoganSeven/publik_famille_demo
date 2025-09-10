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

from quixote import get_publisher, get_request

from wcs.api_utils import get_query_flag
from wcs.qommon import _


class PickableClamD:
    @property
    def clamd(self):
        if getattr(self, '_clamd', None) is None:
            self._clamd = {}
        return self._clamd

    def clamd_json(self):
        data = self.get_view_clamd_data()
        data['span_msg'] = str(data['span_msg'])
        return data

    def has_been_scanned(self):
        return 'returncode' in self.clamd

    def force_not_malware(self):
        if get_publisher().has_site_option('enable-clamd'):
            self.clamd['returncode'] = 100  # forced-ok

    def has_malware(self):
        return self.has_been_scanned() and bool(self.clamd['returncode'] == 1)

    def has_scan_error(self):
        return self.has_been_scanned() and bool(self.clamd['returncode'] == 2)

    def allow_download(self, formdata=None):
        if not get_publisher().has_site_option('enable-clamd'):
            return True
        if (
            formdata
            and get_request()
            and formdata.is_submitter(get_request().user)
            and not get_request().is_in_backoffice()
        ):
            # always allow submitter to download its file
            return True
        if get_query_flag('force-download') and get_request().user and get_request().user.is_admin:
            # allow admin user to force download
            return True
        return self.has_been_scanned() and not self.has_malware() and not self.has_scan_error()

    def get_view_clamd_data(self):
        span_class, span_msg = '', ''
        if not self.has_been_scanned():
            span_class, span_msg = 'waiting-for-scan-file', _(
                'The file is waiting to be checked for malware.'
            )
        elif self.has_malware():
            span_class, span_msg = 'malware-file', _('A malware was found in this file.')
        elif self.has_scan_error():
            span_class, span_msg = 'scan-error-file', _('The file could not be checked for malware.')

        return {
            'digest': self.get_file_digest() if hasattr(self, 'get_file_digest') else self.file_digest(),
            'span_class': span_class,
            'span_msg': span_msg,
        }

    def get_view_clamd_status(self):
        if not get_publisher().has_site_option('enable-clamd'):
            return ''
        return (
            ' <span class="%(span_class)s" data-clamd-digest="%(digest)s">%(span_msg)s</span>'
            % self.get_view_clamd_data()
        )
