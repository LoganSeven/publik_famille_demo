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
from django.core.exceptions import MultipleObjectsReturned
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS
from django.db.models.query import Q
from django.utils.encoding import force_str


class Command(BaseCommand):
    help = "Change a user's password for django.contrib.auth."

    requires_system_checks = []

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
        p = getpass.getpass(prompt=force_str(prompt))
        if not p:
            raise CommandError('aborted')
        return p

    def handle(self, *args, **options):
        username = options['username']
        if not username:
            username = getpass.getuser()

        UserModel = get_user_model()

        qs = UserModel._default_manager.using(options.get('database'))
        qs = qs.filter(Q(uuid=username) | Q(username=username) | Q(email__iexact=username))
        try:
            u = qs.get()
        except UserModel.DoesNotExist:
            raise CommandError("user '%s' does not exist" % username)
        except MultipleObjectsReturned:
            while True:
                print('Select a user:')
                for i, user in enumerate(qs):
                    print('%d.' % (i + 1), user)
                print('> ', end=' ')
                try:
                    j = input()
                except SyntaxError:
                    print('Please enter an integer')
                    continue
                if not isinstance(j, int):
                    print('Please enter an integer')
                    continue
                try:
                    u = qs[j - 1]
                    break
                except IndexError:
                    print('Please enter an integer between 1 and %d' % qs.count())
                    continue

        self.stdout.write("Changing password for user '%s'\n" % u)

        MAX_TRIES = 3
        count = 0
        p1, p2 = 1, 2  # To make them initially mismatch.
        while p1 != p2 and count < MAX_TRIES:
            p1 = self._get_pass()
            p2 = self._get_pass('Password (again): ')
            if p1 != p2:
                self.stdout.write('Passwords do not match. Please try again.\n')
                count = count + 1

        if count == MAX_TRIES:
            raise CommandError("Aborting password change for user '%s' after %s attempts" % (u, count))

        u.set_password(p1)
        u.save()

        return "Password changed successfully for user '%s'" % u
