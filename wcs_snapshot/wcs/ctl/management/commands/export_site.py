# w.c.s. - web application for online forms
# Copyright (C) 2019-2023  Entr'ouvert
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

from wcs.admin.settings import SiteExporterJob
from wcs.qommon import get_cfg

from . import TenantCommand


class Command(TenantCommand):
    help = 'Export the site'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--output', metavar='FILE', default=None, help='name of a file to write output to'
        )

    def handle(self, domain, output, **options):
        self.init_tenant_publisher(domain)
        dirs = [x for x in SiteExporterJob.get_xml_exports_directories() if x not in ('roles', 'settings')]
        if not get_cfg('sp', {}).get('idp-manage-roles'):
            dirs.append('roles')
        exporter = SiteExporterJob(dirs, settings=True)
        with open(output, 'wb') as output:
            output.write(exporter.get_export_file())
