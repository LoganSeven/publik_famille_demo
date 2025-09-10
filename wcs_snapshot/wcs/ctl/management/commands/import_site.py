# w.c.s. - web application for online forms
# Copyright (C) 2019  Entr'ouvert
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

import os

from django.core.management.base import CommandError

from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.workflows import Workflow

from . import TenantCommand


class Command(TenantCommand):
    help = 'Import an exported site'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('filename', metavar='FILENAME', type=str, help='name of file to import')
        parser.add_argument(
            '--if-empty', action='store_true', default=False, help='Import only if site is empty'
        )

    def handle(self, filename, domain, if_empty, **options):
        publisher = self.init_tenant_publisher(domain)
        if not os.path.exists(filename):
            raise CommandError('missing file: %s' % filename)

        # do not reconfigure if the 'if_empty' option is provided
        is_empty = FormDef.count() == 0 and Category.count() == 0 and Workflow.count() == 0
        if if_empty and not is_empty:
            return

        with open(filename, 'rb') as fd:
            publisher.import_zip(fd)
        publisher.cleanup()
