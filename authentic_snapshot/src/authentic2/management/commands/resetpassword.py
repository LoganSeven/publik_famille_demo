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

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS

from authentic2.models import PasswordReset
from authentic2.utils.misc import generate_password

User = get_user_model()


class Command(BaseCommand):
    help = "Reset a user's password for django.contrib.auth."

    require_model_validation = False

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', type=str)
        parser.add_argument(
            '--database',
            action='store',
            dest='database',
            default=DEFAULT_DB_ALIAS,
            help='Specifies the database to use. Default is "default".',
        )

    def _get_pass(self, prompt='Password: '):
        p = getpass.getpass(prompt=prompt)
        if not p:
            raise CommandError('aborted')
        return p

    def handle(self, *args, **options):
        username = options['username']
        if not username:
            username = getpass.getuser()

        try:
            u = User._default_manager.using(options.get('database')).get(**{User.USERNAME_FIELD: username})
        except User.DoesNotExist:
            raise CommandError("user '%s' does not exist" % username)

        p1 = generate_password()
        self.stdout.write("Changing password for user '%s' to '%s'\n" % (u, p1))
        u.set_password(p1)
        u.save()
        PasswordReset.objects.get_or_create(user=u)
        return (
            'Password changed successfully for user "%s", on next login he will be forced to change its'
            ' password.' % u
        )
