# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
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

import argparse
import os.path
import runpy
import sys

from wcs.qommon import force_str
from wcs.qommon.publisher import get_publisher_class

from . import TenantCommand


class Command(TenantCommand):
    '''Run a script within a given host publisher context'''

    support_all_tenants = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--app-dir', metavar='DIR', action='store', dest='app_dir', default=None)
        parser.add_argument('args', nargs=argparse.REMAINDER)

    def handle(self, *args, **options):
        self.disable_sentry()
        if options.get('app_dir'):
            get_publisher_class().APP_DIR = options.get('app_dir')
        domains = self.get_domains(**options)
        fullpath = os.path.dirname(os.path.abspath(args[0]))
        sys.path.insert(0, fullpath)
        module_name = os.path.splitext(os.path.basename(args[0]))[0].encode('utf-8')
        for domain in domains:
            sys.argv = args[:]
            self.init_tenant_publisher(domain, register_tld_names=False)
            runpy.run_module(force_str(module_name))
