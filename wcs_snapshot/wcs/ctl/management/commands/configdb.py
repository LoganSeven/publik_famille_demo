# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

import sys

from django.core.management.base import BaseCommand

from wcs.qommon.publisher import get_publisher_class


class Command(BaseCommand):
    help = 'Migrate databases'

    def add_arguments(self, parser):
        parser.add_argument('-d', '--domain', '--vhost', required=True, metavar='DOMAIN')
        parser.add_argument('--info', action='store_true', help='display current configuration')
        parser.add_argument('--database', help='set database name')
        parser.add_argument('--host', help='set database server hostname')
        parser.add_argument('--port', type=int, help='set database server port')
        parser.add_argument('--user', help='set database server username')
        parser.add_argument('--password', help='set databas server password')

    def handle(self, verbosity, domain=None, **options):
        param_names = ['database', 'host', 'port', 'user', 'password']
        publisher_class = get_publisher_class()
        publisher = publisher_class.create_publisher()
        publisher.set_tenant_by_hostname(domain)
        if options.get('info'):
            for name in param_names:
                sys.stdout.write(f'* {name}: {publisher.cfg["postgresql"].get(name) or "-"}\n')
        elif {x for x in options if options.get(x)}.intersection(param_names):
            params = {}
            for k in param_names:
                params[k] = options.get(k)
            publisher.cfg['postgresql'].update(params)
            publisher.write_cfg()
        else:
            sys.stderr.write('Missing parameters, --info or database settings are required.\n')
