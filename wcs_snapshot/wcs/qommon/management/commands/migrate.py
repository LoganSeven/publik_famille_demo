# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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

import quixote
from django.core.management.base import BaseCommand

from wcs.qommon.publisher import get_publisher_class


class Command(BaseCommand):
    help = 'Migrate databases'

    def handle(self, verbosity=1, **options):
        Publisher = get_publisher_class()
        quixote.cleanup()
        pub = Publisher.create_publisher()
        tenants = list(Publisher.get_tenants())
        nb_tenants = len(tenants)
        for n, tenant in enumerate(tenants, start=1):
            pub = Publisher.create_publisher()
            pub.set_tenant(tenant)
            if pub.has_postgresql_config():
                if verbosity:
                    print('Running migrations for', tenant.hostname, '(%d/%d)' % (n, nb_tenants), flush=True)
                pub.set_sql_application_name('wcs-migrate')
                pub.migrate_sql()
                pub.cleanup()
            quixote.cleanup()
