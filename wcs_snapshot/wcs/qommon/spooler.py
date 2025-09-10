# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

from uwsgidecorators import spool  # pylint: disable=import-error


@spool
def run_after_job(args):
    from django.conf import settings

    subprocess.run(
        [
            settings.WCS_MANAGE_COMMAND,
            'runjob',
            '--domain',
            args['tenant_dir'].strip('/').split('/')[-1],
            '--job-id',
            args['job_id'],
        ],
        check=False,
    )
