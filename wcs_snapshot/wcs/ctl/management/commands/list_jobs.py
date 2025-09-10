# w.c.s. - web application for online forms
# Copyright (C) 2005-2025  Entr'ouvert
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
import time

from django.utils.timezone import now

from wcs.qommon.afterjobs import AfterJob
from wcs.sql_criterias import Contains, Equal

from . import TenantCommand


class Command(TenantCommand):
    help = 'List jobs'
    support_all_tenants = True
    defaults_to_all_tenants = True
    watch_delay = 1

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--status',
            metavar='STATUS',
            default='running',
            help='limit to given status (aborted, registered, running, completed, failed, or all)',
        )
        parser.add_argument(
            '--sort',
            metavar='ORDER',
            default='creation_time',
            choices=['creation_time', 'completion_time'],
            help='creation_time (default), completion_time',
        )
        parser.add_argument('--reverse', action='store_true', default=False)
        parser.add_argument('--job-id', metavar='ID')
        parser.add_argument(
            '--watch', action='store_true', default=False, help='do not quit, refresh a (single) job line'
        )

    def handle(self, *args, **options):
        self.criterias = []
        if options.get('status') != 'all':
            self.criterias.append(Contains('status', options.get('status').split(',')))
        if options.get('job_id'):
            self.criterias = [Equal('id', options.get('job_id'))]

        jobs = []
        len_longest_domain = 0
        for domain in self.get_domains(**options):
            self.init_tenant_publisher(domain, register_tld_names=False)
            match = False
            for job in AfterJob.select(self.criterias):
                match = True
                job._domain = domain
                jobs.append(job)
            if match:
                len_longest_domain = max(len_longest_domain, len(domain))

        old_date = now() - datetime.timedelta(days=365)
        jobs.sort(key=lambda x: getattr(x, options.get('sort')) or old_date)
        if options.get('reverse'):
            jobs.reverse()

        if options.get('watch') and options.get('job_id') and jobs:
            job = jobs[0]
            try:
                latest_status = job.status
                domain = job._domain
                while True:
                    self.print_job(job, len_longest_domain, options)
                    self.stdout.write('\r', ending='')
                    time.sleep(self.watch_delay)
                    job = job.get(job.id)
                    job._domain = domain
                    if job.status != latest_status:
                        latest_status = job.status
                        self.stdout.write('\7\b', ending='')  # bell on status change
            except KeyboardInterrupt:
                pass
        else:
            for job in jobs:
                self.print_job(job, len_longest_domain, options)
                self.stdout.write('')

    def print_job(self, job, len_longest_domain, options):
        if not options.get('domain'):
            self.stdout.write(f'{job._domain.ljust(len_longest_domain)}', ending='  ')
        self.stdout.write(
            f'{str(job.creation_time)[:19]}  {job.id}  {job.typename:25s}  {job.status:10s}', ending=''
        )
        if job.completion_time:
            self.stdout.write(f' ({str(job.completion_time)[:19]})', ending='')
        if job.current_count and job.total_count:
            self.stdout.write(
                f'  {job.current_count}/{job.total_count} ({job.current_count/job.total_count*100:.0f}%)',
                ending='',
            )
