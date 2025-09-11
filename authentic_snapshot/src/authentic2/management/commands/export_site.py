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

import json
import sys

from django.core.management.base import BaseCommand

from authentic2.data_transfer import export_site


class Command(BaseCommand):
    help = 'Export site'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', metavar='FILE', default=None, help='name of a file to write output to'
        )

    def handle(self, *args, **options):
        if options['output']:
            with open(options['output'], 'w') as f:
                json.dump(export_site(), f, indent=4)
        else:
            json.dump(export_site(), sys.stdout, indent=4)
