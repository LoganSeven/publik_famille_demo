# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

from django.core.management.commands import makemessages


class Command(makemessages.Command):
    xgettext_options = makemessages.Command.xgettext_options + ['--keyword=N_']

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--keep-obsolete', action='store_true', help='Keep obsolete message strings.')

    def handle(self, *args, **options):
        if not options.get('add_location') and self.gettext_version >= (0, 19):
            options['add_location'] = 'file'
        options['no_obsolete'] = not (options.get('keep_obsolete'))
        return super().handle(*args, **options)
