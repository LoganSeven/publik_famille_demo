# w.c.s. - web application for online forms
# Copyright (C) 2005-2021  Entr'ouvert
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

import collections

from django.core.management.base import CommandError
from django.db.backends.postgresql.client import DatabaseClient

from . import TenantCommand


class PsqlDatabaseClient(DatabaseClient):
    def __init__(self, pub):
        # emulate a connection object
        self.connection = collections.namedtuple('Connection', ['settings_dict'])({})
        for key, value in pub.cfg['postgresql'].items():
            if key == 'database':
                key = 'name'
            self.connection.settings_dict[key.upper()] = value
        super().__init__(self.connection)


class Command(TenantCommand):
    def handle(self, *args, **options):
        if not options['domain']:
            raise CommandError('missing hostname')
        pub = self.init_tenant_publisher(options['domain'], register_tld_names=False)
        PsqlDatabaseClient(pub).runshell(())  # noqa pylint: disable=too-many-function-args
