# w.c.s. - web application for online forms
# Copyright (C) 2005-2014  Entr'ouvert
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
import sys

import setproctitle
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now

from wcs import sql
from wcs.qommon.cron import CronJob, cron_worker, get_jobs_since
from wcs.qommon.publisher import get_publisher_class


class Command(BaseCommand):
    help = 'Execute cronjobs'

    def add_arguments(self, parser):
        parser.set_defaults(verbosity=0)
        parser.add_argument('-d', '--domain', '--vhost', metavar='DOMAIN')
        parser.add_argument(
            '--force-job',
            dest='force_job',
            action='store_true',
            help='Run even if DISABLE_CRON_JOBS is set in settings',
        )
        parser.add_argument('--job', dest='job_name', metavar='NAME')

    def handle(self, verbosity, domain=None, job_name=None, **options):
        if getattr(settings, 'DISABLE_CRON_JOBS', False) and not options['force_job']:
            if verbosity > 1:
                print('Command is ignored because DISABLE_CRON_JOBS is set in settings')
            return
        if domain:
            single_tenant = True
            domains = [domain]
        else:
            single_tenant = False
            domains = [x.hostname for x in get_publisher_class().get_tenants()]
        if not job_name and verbosity > 2:
            print('cron start')
        publisher_class = get_publisher_class()
        publisher_class.register_cronjobs()
        publisher = publisher_class.create_publisher()
        offset = ord(settings.SECRET_KEY[-1]) % 60
        if not job_name:
            CronJob.log('starting cron (minutes offset is %s)' % offset, in_tenant=False)

        if not domain and not options['force_job']:
            # exit early if maximum number of workers has been reached
            running = 0
            stalled_tenants = []
            for domain in domains:
                publisher.set_tenant_by_hostname(domain)
                if not publisher.has_postgresql_config():
                    continue
                publisher.set_sql_application_name('wcs-cron')
                status, timestamp = sql.get_cron_status()
                if status == 'running':
                    running += 1
                    if timestamp and now() - timestamp > datetime.timedelta(hours=6):
                        stalled_tenants.append(domain)
                        CronJob.log('stalled tenant: %s' % domain)
                        sql.mark_cron_status('done')

            if stalled_tenants:
                raise CommandError('aborting, stalled tenants: %s' % ', '.join(stalled_tenants))
            if running >= settings.CRON_WORKERS:
                CronJob.log('skipped, too many workers')
                return

        for domain in domains:
            publisher.set_tenant_by_hostname(domain)
            if publisher.get_site_option('disable_cron_jobs', 'variables'):
                if verbosity > 1:
                    print('cron ignored on %s because DISABLE_CRON_JOBS is set' % domain)
                continue
            if not publisher.has_postgresql_config():
                if verbosity > 1:
                    print('cron ignored on %s because it has no PostgreSQL configuration' % domain)
                continue
            publisher.set_sql_application_name('wcs-cron')

            if job_name:
                # a specific job name is asked, run it whatever
                # the current time is.
                jobs = [x for x in publisher.cronjobs if x.name == job_name]
            else:
                # accumulate jobs that must be run since last time
                if single_tenant:
                    timestamp = now()
                else:
                    timestamp = sql.get_cron_status()[1]
                jobs = get_jobs_since(publisher, timestamp or now())

                if sql.has_needed_reindex():
                    # delayed migrations
                    jobs = [CronJob(publisher.reindex_sql, name='reindex')] + list(jobs)

                if timestamp and not jobs:
                    # do not skip on first run (with timestamp as None), so
                    # a first execution timestamp is written in the database.
                    if verbosity > 1:
                        print('cron skipped on %s (no job)' % domain)
                    continue

            if single_tenant:
                cron_status, timestamp = 'ignored', now()
            else:
                cron_status, timestamp = sql.get_and_update_cron_status()
            if not options['force_job']:
                if cron_status == 'running':
                    if verbosity > 1:
                        print(domain, 'skip running, already handled')
                    continue
            setproctitle.setproctitle(sys.argv[0] + ' cron [%s]' % domain)
            if verbosity > 1:
                print('cron work on %s' % domain)
            CronJob.log('start')
            try:
                cron_worker(publisher, jobs, single_job=bool(job_name))
            except Exception as e:
                CronJob.log('aborted (%r)' % e)
                publisher.capture_exception(sys.exc_info())
                raise e
            finally:
                if not single_tenant:
                    sql.mark_cron_status('done')

        if verbosity > 1:
            print('cron end')
