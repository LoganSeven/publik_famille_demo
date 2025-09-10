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
from django.db.models import Count
from django.utils.timezone import now

from lingo.invoicing.models import Campaign, DraftInvoice, DraftInvoiceLine, DraftJournalLine, Pool


class Command(BaseCommand):
    help = 'Clear old draft pools from finalized campaigns'

    def handle(self, **options):
        with_many_draft_pools = (
            Pool.objects.filter(draft=True)
            .values('campaign')
            .alias(count=Count('campaign'))
            .order_by()
            .filter(count__gt=1)
        )
        # delete draft pools for finalized campaigns, after 31 days
        queryset = Campaign.objects.filter(
            finalized=True, updated_at__lte=now() - datetime.timedelta(days=31), pk__in=with_many_draft_pools
        )
        for campaign in queryset:
            for pool in campaign.pool_set.order_by('created_at'):
                if not pool.campaign.pool_set.filter(created_at__gt=pool.created_at, draft=True).exists():
                    # but keep last draft
                    continue
                DraftJournalLine.objects.filter(pool=pool).delete()
                DraftInvoiceLine.objects.filter(pool=pool).delete()
                DraftInvoice.objects.filter(pool=pool).delete()
                pool.delete()
