# lingo - payment and billing system
# Copyright (C) 2022-2025  Entr'ouvert
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

from django.conf import settings
from django.core.management.base import BaseCommand

from lingo.invoicing.models import PoolAsyncJob


class Command(BaseCommand):
    help = 'Run Pool jobs'

    def handle(self, **options):
        if PoolAsyncJob.objects.filter(status='running').count() >= settings.POOL_MAX_RUNNING_JOBS:
            return

        jobs = PoolAsyncJob.objects.filter(status__in=['registered', 'waiting']).order_by(
            'creation_timestamp'
        )
        for job in jobs:
            # run job is order of creation, but skip waiting jobs which are not ready
            if job.status == 'waiting':
                if not job.is_ready:
                    continue
            job.run(cron=False)
            break
