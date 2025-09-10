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
from django.db.transaction import atomic
from django.utils.timezone import now

from lingo.epayment.models import Transaction


class Command(BaseCommand):
    def handle(self, **options):
        for transaction in Transaction.objects.filter(
            start_date__gte=now() - datetime.timedelta(hours=3),
            start_date__lte=now() - datetime.timedelta(minutes=5),
            status__in=Transaction.RUNNING_STATUSES,
        ):
            with atomic():
                transaction.check_status()
