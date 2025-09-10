# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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
import os
import sys
import time
from contextlib import contextmanager

import psutil
from django.conf import settings
from django.utils.timezone import localtime
from quixote import get_publisher


class CronJob:
    name = None
    hours = None
    minutes = None
    weekdays = None
    days = None
    function = None

    LONG_JOB_DURATION = 2 * 60  # 2 minutes
    LONG_JOB_CPU_DURATION = 60  # 1 minute of full CPU time

    def __init__(self, function, name=None, hours=None, minutes=None, weekdays=None, days=None):
        self.function = function
        self.name = name
        self.hours = hours
        self.minutes = minutes
        self.weekdays = weekdays
        self.days = days

    @contextmanager
    def log_long_job(
        self,
        obj_description=None,
        *,
        record_long_duration=None,
        record_long_cpu_duration=None,
        record_error_kwargs=None,
    ):
        start = time.perf_counter()
        process_start = time.process_time()
        yield
        process_duration = time.process_time() - process_start
        duration = time.perf_counter() - start
        if duration > self.LONG_JOB_DURATION or process_duration > self.LONG_JOB_CPU_DURATION:
            minutes = int(duration / 60)
            process_minutes = int(process_duration / 60)
            if obj_description:
                self.log(
                    '%s: running on "%s" took %d minutes, %d CPU minutes'
                    % (self.name, obj_description, minutes, process_minutes)
                )
            else:
                self.log(
                    'long job: %s (took %s minutes, %d CPU minutes)' % (self.name, minutes, process_minutes)
                )

            if record_error_kwargs and (
                (duration > (record_long_duration or (self.LONG_JOB_DURATION * 10)))
                or (process_duration > (record_long_cpu_duration or (self.LONG_JOB_CPU_DURATION * 10)))
            ):
                with get_publisher().error_context(duration=duration, process_duration=process_duration):
                    get_publisher().record_error(**record_error_kwargs)

    @classmethod
    def log(cls, message, in_tenant=True):
        now = localtime()
        if in_tenant:
            base_dir = get_publisher().tenant.directory
        else:
            base_dir = get_publisher().APP_DIR
        log_dir = os.path.join(base_dir, 'cron-logs', now.strftime('%Y'))
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'cron.log-%s' % now.strftime('%Y%m%d')), 'a+') as fd:
            fd.write('%s [%s] %s\n' % (now.isoformat(), os.getpid(), message.replace('\n', ' ')))

    def log_debug(self, message, in_tenant=True):
        if get_publisher().get_site_option('cron-log-level') != 'debug':
            return
        memory = psutil.Process().memory_info().rss / (1024 * 1024)
        self.log(f'(mem: {memory:.1f}M) {message}', in_tenant=in_tenant)

    @classmethod
    def log_sql(cls, message, in_tenant=True):
        cls.log(f'SQL: {message}', in_tenant=in_tenant)

    def is_time(self, timetuple):
        minutes = self.minutes
        if minutes:
            # will set minutes to an arbitrary value based on installation, this
            # prevents waking up all jobs at the same time on a container farm.
            minutes = [(x + ord(settings.SECRET_KEY[-1])) % 60 for x in minutes]
        if self.days and timetuple[2] not in self.days:
            return False
        if self.weekdays and timetuple[6] not in self.weekdays:
            return False
        if self.hours and timetuple[3] not in self.hours:
            return False
        if minutes and timetuple[4] not in minutes:
            return False
        return True


def get_jobs_since(publisher, since):
    timestamp = localtime(since)
    t_now = localtime()
    jobs = set()
    while timestamp <= t_now:
        for job in publisher.cronjobs:
            if job not in jobs and job.is_time(timestamp.timetuple()):
                jobs.add(job)
        timestamp += datetime.timedelta(minutes=1)
    return jobs


def run_jobs(publisher, jobs):
    for job in jobs:
        publisher.after_jobs = []
        publisher.current_cron_job = job
        publisher.install_lang()
        publisher.setup_timezone()
        publisher.reset_formdata_state()
        publisher.set_sql_application_name(f'wcs-cron-{job.name}')
        try:
            with job.log_long_job():
                job.function(publisher, job=job)
            publisher.process_after_jobs(spool=False)
        except Exception as e:
            job.log(f'exception running job {job.name}: {e}')
            publisher.capture_exception(sys.exc_info())


def cron_worker(publisher, jobs, single_job=False):
    import wcs.sql

    CronJob.log('running jobs: %r' % sorted([x.name or x for x in jobs]))
    wcs.sql.LoggingCursor.queries_count = 0
    if get_publisher().get_site_option('cron-log-level') == 'debug':
        wcs.sql.LoggingCursor.queries_log_function = CronJob.log_sql
    process_start = time.process_time()
    memory_start = psutil.Process().memory_info().rss / (1024 * 1024)

    t_start = localtime()
    seen_jobs = set()
    while jobs:
        run_jobs(publisher, jobs)

        if single_job:
            break

        # catch up jobs that would have been started when jobs were running
        seen_jobs.update([x.name for x in jobs])
        jobs = [x for x in get_jobs_since(publisher, t_start) if x.name not in seen_jobs]
        if jobs:
            CronJob.log('running more jobs: %r' % sorted([x.name or x for x in jobs]))

    wcs.sql.LoggingCursor.queries_log_function = None
    process_end = time.process_time()
    memory_end = psutil.Process().memory_info().rss / (1024 * 1024)
    CronJob.log(
        'resource usage summary: CPU time: %.2fs / Memory: %.2fM / SQL queries: %s'
        % (
            process_end - process_start,
            memory_end - memory_start,
            wcs.sql.LoggingCursor.queries_count,
        )
    )
