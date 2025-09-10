# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
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

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.invoicing.models import PaymentType, Regie
from lingo.pricing.models import CriteriaCategory, Pricing


class Command(BaseCommand):
    help = 'Clear obsolete snapshot instances'

    def handle(self, **options):
        for model in [Agenda, CheckTypeGroup, Pricing, CriteriaCategory, Regie]:
            queryset = model.snapshots.filter(updated_at__lte=now() - datetime.timedelta(days=1))
            if model == Regie:
                PaymentType.objects.filter(regie__in=queryset).delete()
            queryset.delete()
