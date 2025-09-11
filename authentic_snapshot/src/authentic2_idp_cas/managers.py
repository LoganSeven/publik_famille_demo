# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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

import urllib.parse
from datetime import timedelta

from django.db import models
from django.db.models import query
from django.utils.timezone import now


class TicketQuerySet(query.QuerySet):
    def clean_expired(self):
        '''Remove expired tickets'''
        self.filter(expire__gte=now()).delete()

    def cleanup(self):
        '''Delete old tickets'''
        qs = self.filter(expire__lt=now())
        qs |= self.filter(expire__isnull=True, creation__lt=now() - timedelta(seconds=300))
        qs.delete()


class ServiceQuerySet(query.QuerySet):
    def for_service(self, service):
        '''Find service with the longest match'''
        parsed = urllib.parse.urlparse(service)
        matches = []
        for match in self.filter(urls__contains=parsed.netloc):
            urls = match.get_urls()
            for url in urls:
                if service.startswith(url):
                    matches.append((len(url), match))
        if not matches:
            return None
        matches.sort()
        return matches[0][1]


ServiceManager = models.Manager.from_queryset(ServiceQuerySet)

TicketManager = models.Manager.from_queryset(TicketQuerySet)
