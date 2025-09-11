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

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import translation

from authentic2.data_transfer import ImportContext, import_site


class DryRunException(Exception):
    pass


def create_context_args(options):
    kwargs = {}
    if options['option']:
        for context_op in options['option']:
            context_op = context_op.replace('-', '_')
            if context_op.startswith('no_'):
                kwargs[context_op[3:]] = False
            else:
                kwargs[context_op] = True
    return kwargs


class Command(BaseCommand):
    help = 'Import site'

    def add_arguments(self, parser):
        parser.add_argument('filename', metavar='FILENAME', type=str, help='name of file to import')
        parser.add_argument(
            '--dry-run', action='store_true', dest='dry_run', help='Do not actually perform the import'
        )
        parser.add_argument(
            '-o',
            '--option',
            action='append',
            help='Import context options',
            choices=[
                'role-delete-orphans',
                'ou-delete-orphans',
                'no-role-permissions-update',
                'no-role-attributes-update',
                'no-role-parentings-update',
                'no-role-parentings-update',
                'set-absent-ou-to-default',
            ],
        )

    def handle(self, filename, **options):
        translation.activate(settings.LANGUAGE_CODE)
        dry_run = options['dry_run']
        msg = 'Dry run\n' if dry_run else 'Real run\n'
        c_kwargs = create_context_args(options)
        try:
            with open(filename) as f:
                with transaction.atomic():
                    sys.stdout.write(msg)
                    result = import_site(json.load(f), ImportContext(**c_kwargs))
                    if dry_run:
                        raise DryRunException()
        except DryRunException:
            pass
        sys.stdout.write(result.to_str())
        sys.stdout.write('Success\n')
        translation.deactivate()
