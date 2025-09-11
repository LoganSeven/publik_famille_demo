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

from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import Role

from ...decorators import to_list


@to_list
def get_instances(ctx):
    return [None]


@to_list
def get_attribute_names(instance, ctx):
    yield ('is_superuser', 'is_superuser (%s)' % _('role attribute'))


def get_dependencies(instance, ctx):
    return (
        'user',
        'service',
    )


def get_attributes(instance, ctx):
    user = ctx.get('user')
    service = ctx.get('service')
    if not user or not service:
        return ctx
    ctx = ctx.copy()
    roles = Role.objects.for_user(user).filter(service=service)
    for service_role in roles:
        if service_role.is_superuser:
            ctx['is_superuser'] = True
    return ctx
