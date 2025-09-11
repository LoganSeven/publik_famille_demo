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

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import Role

from ...decorators import to_list
from ...models import Attribute, AttributeValue


@to_list
def get_instances(ctx):
    """
    Retrieve instances from settings
    """
    return [None]


@to_list
def get_attribute_names(instance, ctx):
    User = get_user_model()
    for field in User._meta.fields:
        if field.name == 'ou':
            continue
        name = 'django_user_' + str(field.name)
        description = field.verbose_name + ' (%s)' % name
        yield name, description

    yield 'django_user_ou_uuid', _('OU UUIDs') + ' (django_user_ou_uuid)'
    yield 'django_user_ou_slug', _('OU slug') + ' (django_user_ou_slug)'
    yield 'django_user_ou_name', _('OU name') + ' (django_user_ou_name)'

    for attribute in Attribute.objects.all():
        name = 'django_user_' + str(attribute.name)
        description = attribute.label + ' (%s)' % name
        yield name, description
    group_label = User._meta.get_field('groups').verbose_name
    yield 'django_user_groups', group_label + ' (django_user_groups)'
    yield 'django_user_group_names', group_label + ' (django_user_group_names)'
    yield 'django_user_domain', _('User domain') + ' (django_user_domain)'
    yield 'django_user_identifier', _('User identifier') + ' (django_user_identifier)'
    yield 'django_user_full_name', _('Full name') + ' (django_user_full_name)'
    yield 'a2_role_slugs', _('Role slugs')
    yield 'a2_role_names', _('Role names')
    yield 'a2_role_uuids', _('Role UUIDs')
    yield 'a2_service_ou_role_slugs', _('Role slugs from same organizational unit as the service')
    yield 'a2_service_ou_role_names', _('Role names from same organizational unit as the service')
    yield 'a2_service_ou_role_uuids', _('Role uuids from same organizational unit as the service')


def get_dependencies(instance, ctx):
    return ('user', 'request')


def get_attributes(instance, ctx):
    user = ctx.get('user')
    User = get_user_model()
    if not user or not isinstance(user, User):
        return ctx
    for field in User._meta.fields:
        if field.name == 'ou':
            continue
        value = getattr(user, field.name)
        if value is None:
            continue
        ctx['django_user_' + str(field.name)] = getattr(user, field.name)
    # set OU value
    if user.ou:
        for attr in ('uuid', 'slug', 'name'):
            ctx['django_user_ou_' + attr] = getattr(user.ou, attr)
    for av in AttributeValue.objects.with_owner(user).select_related('attribute'):
        serialize = av.attribute.get_kind().get('attributes_ng_serialize', lambda a, b: b)
        value = av.to_python()
        serialized = serialize(ctx, value)
        ctx['django_user_' + str(av.attribute.name)] = serialized
        ctx['django_user_' + str(av.attribute.name) + ':verified'] = av.verified
    ctx['django_user_groups'] = [group for group in user.groups.all()]
    ctx['django_user_group_names'] = [str(group) for group in user.groups.all()]
    if user.username:
        splitted = user.username.rsplit('@', 1)
        ctx['django_user_domain'] = splitted[1] if '@' in user.username else ''
        ctx['django_user_identifier'] = splitted[0]
    ctx['django_user_full_name'] = user.get_full_name()
    roles = Role.objects.for_user(user)
    ctx['a2_role_slugs'] = roles.values_list('slug', flat=True)
    ctx['a2_role_names'] = roles.values_list('name', flat=True)
    ctx['a2_role_uuids'] = roles.values_list('uuid', flat=True)
    if 'service' in ctx and getattr(ctx['service'], 'ou', None):
        ou = ctx['service'].ou
        ctx['a2_service_ou_role_slugs'] = roles.filter(ou=ou).values_list('slug', flat=True)
        ctx['a2_service_ou_role_names'] = roles.filter(ou=ou).values_list('name', flat=True)
        ctx['a2_service_ou_role_uuids'] = roles.filter(ou=ou).values_list('uuid', flat=True)
    return ctx
