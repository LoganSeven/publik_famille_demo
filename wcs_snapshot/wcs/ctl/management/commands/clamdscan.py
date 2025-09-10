# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

import itertools

from wcs.carddef import CardDef
from wcs.clamd import scan_formdata
from wcs.formdef import FormDef
from wcs.sql_criterias import StrictNotEqual

from . import TenantCommand


class Command(TenantCommand):
    help = '''Scan attached files for malware'''
    support_all_tenants = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--rescan',
            action='store_true',
            help='scan already scanned files',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='scan even if option enable-clamd is not set on the domain',
        )

    def handle(self, *args, **options):
        for domain in self.get_domains(**options):
            publisher = self.init_tenant_publisher(domain, register_tld_names=False)
            if not publisher.has_site_option('enable-clamd') and not options.get('force'):
                print('Ignoring %s because clamd is not enabled.' % domain)
                continue
            infected_formdata = []
            criterias = [StrictNotEqual('status', 'draft')]
            for formdef in itertools.chain(FormDef.select(), CardDef.select()):
                for formdata in formdef.data_class().select_iterator(criterias, itersize=200):
                    if scan_formdata(
                        formdata, rescan=options.get('rescan'), verbose=bool(options['verbosity'] > 1)
                    ):
                        infected_formdata.append(formdata)
            if infected_formdata:
                print('Malware found in %s.' % domain)
                for formdata in infected_formdata:
                    print(formdata.get_backoffice_url())
            else:
                print('No malware found in %s.' % domain)
