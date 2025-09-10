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

import sys

from django.core.management.base import BaseCommand, CommandError
from quixote import get_publisher

from wcs.qommon.publisher import UnknownTenantError, get_publisher_class


class TenantCommand(BaseCommand):
    support_all_tenants = False
    defaults_to_all_tenants = False

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '-d', '--domain', '--vhost', metavar='DOMAIN', required=not (self.support_all_tenants)
        )
        if self.support_all_tenants:
            parser.add_argument('--all-tenants', action='store_true')
            parser.add_argument('--exclude-tenants', metavar='TENANTS')

    def get_domains(self, **options):
        domain = options.get('domain')
        all_tenants = options.get('all_tenants')
        exclude_tenants = options.get('exclude_tenants')
        if domain and all_tenants:
            raise CommandError('--domain and --all-tenants are exclusive')
        if not (domain or all_tenants) and not self.defaults_to_all_tenants:
            raise CommandError('either --domain or --all-tenants is required')
        if domain:
            domains = [domain]
        else:
            domains = [x.hostname for x in get_publisher_class().get_tenants()]
        domains = [x for x in domains if x not in (exclude_tenants or '').split(',')]
        return domains

    def execute(self, *args, **kwargs):
        if get_publisher():
            # a publisher object will already be existing when the command is used
            # via call_command() in tests; clean it up properly so database connections
            # are not left lingering.
            get_publisher().cleanup()
        return super().execute(*args, **kwargs)

    def init_tenant_publisher(self, domain, **kwargs):
        publisher = get_publisher_class().create_publisher(**kwargs)
        try:
            publisher.set_tenant_by_hostname(domain)
        except UnknownTenantError:
            raise CommandError('unknown tenant')
        publisher.install_lang()
        publisher.setup_timezone()
        publisher.substitutions.feed(publisher)
        return publisher

    def disable_sentry(self):
        # disable sentry by reinitialising with no parameters
        if 'sentry_sdk' in sys.modules:
            # noqa pylint: disable=import-error
            import sentry_sdk

            sentry_sdk.init()
