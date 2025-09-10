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

from django.core.management import CommandError
from quixote import get_publisher

from wcs.qommon.afterjobs import AfterJob

from . import TenantCommand


class Command(TenantCommand):
    '''Run an afterjob (internal command)'''

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--job-id', action='store', required=True)
        parser.add_argument('--force-replay', action='store_true', default=False)
        parser.add_argument('--raise', action='store_true', default=False)

    def handle(self, *args, **options):
        domain = options.pop('domain')
        self.init_tenant_publisher(domain)
        try:
            job = AfterJob.get(options['job_id'])
        except KeyError:
            raise CommandError('missing job (%s on %s)' % (options['job_id'], domain))
        if options.get('force_replay'):
            job.completion_time = None
        job.raise_exception = options.get('raise')
        job.run()

        # run afterjobs that may have been added
        get_publisher().process_after_jobs(spool=False)
