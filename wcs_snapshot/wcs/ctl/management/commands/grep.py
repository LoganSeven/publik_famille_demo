# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

from django.core.management import CommandError

from wcs.formdef_base import get_formdefs_of_all_kinds
from wcs.utils import grep_strings
from wcs.workflows import Workflow

from . import TenantCommand


class Command(TenantCommand):
    help = '''Grep tenant(s) for a given pattern'''
    support_all_tenants = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--type',
            metavar='TYPE',
            default='strings',
            choices=['strings', 'action-types', 'field-types'],
            help='strings (default), action-types, field-types',
        )
        parser.add_argument('--urls', action='store_true', help='display URLs to hits')
        parser.add_argument('pattern', nargs='+')

    def handle(self, *args, **options):
        self.pattern = ' '.join(options.get('pattern'))
        self.urls = options.get('urls')
        self.seen = set()
        try:
            method = getattr(self, 'grep_' + options.get('type').replace('-', '_'))
        except AttributeError:
            raise CommandError('unknown grep type')
        for domain in self.get_domains(**options):
            self.init_tenant_publisher(domain, register_tld_names=False)
            method()

    def grep_strings(self):
        grep_strings(string=self.pattern, hit_function=self.print_hit)

    def grep_action_types(self):
        for workflow in Workflow.select(ignore_errors=True, ignore_migration=True, order_by='id'):
            for action in workflow.get_all_items():
                url = action.get_admin_url()
                if self.pattern in action.key:
                    self.print_hit(url)

    def grep_field_types(self):
        for formdef in get_formdefs_of_all_kinds(order_by='id'):
            for field in formdef.fields or []:
                url = formdef.get_field_admin_url(field)
                if self.pattern in field.key:
                    self.print_hit(url)

    def print_hit(self, source_url, *args, **kwargs):
        if self.urls:
            seen_key = (source_url,)
        else:
            seen_key = (source_url,) + args
        if seen_key in self.seen:
            return
        self.seen.add(seen_key)
        self.print_unique_hit(*seen_key)

    def print_unique_hit(self, url, *args):
        print(url, *args)
