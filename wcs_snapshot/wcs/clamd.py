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

import subprocess

from django.utils.timezone import now
from quixote import get_publisher, get_response

from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.errors import AccessForbiddenError
from wcs.qommon.misc import localstrftime


def is_valid_returncode(code):
    # from clamdscan manpage:
    # 0: No virus found.
    # 1: Virus(es) found.
    # 2: An error occurred.
    return code in (0, 1)


def scan_formdata(formdata, rescan=False, verbose=False):
    store = False
    malware = False
    for file_data in formdata.get_all_file_data(with_history=False):
        if not file_data.has_been_scanned() or rescan:
            path = (
                file_data.get_fs_filename()
                if hasattr(file_data, 'get_fs_filename')
                else file_data.get_file_path()
            )
            clamd = subprocess.run(
                ['clamdscan', '--fdpass', path], check=False, capture_output=True, text=True
            )
            if (
                file_data.has_been_scanned()
                and is_valid_returncode(file_data.clamd.get('returncode'))
                and not is_valid_returncode(clamd.returncode)
            ):
                # do not store again if there's an error now while we had a valid
                # status on a previous scan.
                pass
            else:
                store = True
                file_data.clamd['returncode'] = clamd.returncode
                file_data.clamd['timestamp'] = localstrftime(now())
                file_data.clamd['clamdscan_output'] = clamd.stdout
                if verbose:
                    print(clamd.stdout)
            malware |= file_data.has_malware()
    if store:
        formdata._store_all_evolution = True
        formdata.store()
    return malware


class ClamDScanJob(AfterJob):
    def __init__(self, formdata, **kwargs):
        super().__init__(
            formdef_class=formdata.formdef.__class__, formdef_id=formdata.formdef.id, formdata_id=formdata.id
        )

    def execute(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        formdata = formdef.data_class().get(self.kwargs['formdata_id'])
        scan_formdata(formdata)


def add_clamd_scan_job(formdata):
    if get_publisher().has_site_option('enable-clamd'):
        if get_response():
            job = get_publisher().add_after_job(ClamDScanJob(formdata=formdata))
            job.store()
        else:
            scan_formdata(formdata)


class AccessForbiddenMalwareError(AccessForbiddenError):
    backoffice_template_name = 'wcs/backoffice/error-malware.html'

    def __init__(self, file_data):
        super().__init__(public_msg=file_data.get_view_clamd_data()['span_msg'])
