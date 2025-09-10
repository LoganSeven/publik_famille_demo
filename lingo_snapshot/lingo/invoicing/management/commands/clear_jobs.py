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

import datetime

from django.core.management.base import BaseCommand
from django.utils.timezone import now

from lingo.invoicing.models import CampaignAsyncJob, PoolAsyncJob


class Command(BaseCommand):
    help = 'Clear jobs'

    def handle(self, **options):
        for klass in [CampaignAsyncJob, PoolAsyncJob]:
            # delete completed jobs after 2 days
            klass.objects.filter(
                status='completed', last_update_timestamp__lte=now() - datetime.timedelta(days=2)
            ).delete()
            # delete failed jobs after 10 days
            klass.objects.filter(
                status='failed', last_update_timestamp__lte=now() - datetime.timedelta(days=10)
            ).delete()
