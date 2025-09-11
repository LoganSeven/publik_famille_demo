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

from authentic2.backends.ldap_backend import LDAPBackend, LDAPUser

from ...decorators import to_list


@to_list
def get_instances(ctx):
    """
    Retrieve instances from settings
    """
    return [None]


def get_attribute_names(instance, ctx):
    return LDAPBackend.get_attribute_names()


def get_dependencies(instance, ctx):
    return ('user',)


def get_attributes(instance, ctx):
    user = ctx.get('user')
    if user and isinstance(user, LDAPUser):
        ctx.update(user.get_attributes(instance, ctx))
    return ctx
