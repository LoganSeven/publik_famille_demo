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

import io
import os

from wcs.qommon.storage import atomic_write

from . import CommandError, TenantCommand


class Command(TenantCommand):
    support_all_tenants = True

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--set', action='store_true', default=True, help='set option')
        parser.add_argument('--unset', action='store_true', default=False, help='unset option')
        parser.add_argument('section', metavar='SECTION')
        parser.add_argument('option', metavar='OPTION')
        parser.add_argument('value', metavar='VALUE', nargs='?')

    def handle(self, *args, **options):
        if options.get('unset') and options.get('value'):
            raise CommandError('a value should not be passed when using --unset')
        if not options.get('unset') and not options.get('value'):
            raise CommandError('a value is required')
        for domain in self.get_domains(**options):
            pub = self.init_tenant_publisher(domain, register_tld_names=False)
            self.apply_change(pub, **options)

    def apply_change(self, publisher, section, option, value=None, unset=False, **kwargs):
        if not publisher.site_options.has_section(section):
            publisher.site_options.add_section(section)
        changed = False
        if unset:
            if option in publisher.site_options[section]:
                del publisher.site_options[section][option]
                changed = True
        else:
            if publisher.site_options[section].get(option) != value:
                publisher.site_options[section][option] = value
                changed = True

        if changed:
            site_options_filename = os.path.join(publisher.app_dir, 'site-options.cfg')
            content_fd = io.StringIO()
            publisher.site_options.write(content_fd)
            atomic_write(site_options_filename, content_fd.getvalue().encode())
