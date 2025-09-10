# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

from django.core.management.commands import shell

from . import TenantCommand


class Command(shell.Command, TenantCommand):
    help = '''Run a shell in tenant context'''

    def add_arguments(self, parser):
        super().add_arguments(parser)
        super(shell.Command, self).add_arguments(parser)

    def handle(self, *args, **options):
        self.disable_sentry()
        domain = options.pop('domain')
        self.init_tenant_publisher(domain, register_tld_names=False)
        return shell.Command().handle(*args, **options)
