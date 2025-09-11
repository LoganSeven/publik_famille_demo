# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouver,
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


try:
    import ldap  # pylint: disable=unused-import
    from ldap.filter import filter_format  # pylint: disable=unused-import
except ImportError:
    ldap = None

from authentic2.backends.ldap_backend import LDAPBackend
from authentic2.base_commands import LogToConsoleCommand


class Command(LogToConsoleCommand):
    loggername = 'authentic2.backends.ldap_backend'

    def core_command(self, *args, **kwargs):
        LDAPBackend.update_mapped_roles_list()
