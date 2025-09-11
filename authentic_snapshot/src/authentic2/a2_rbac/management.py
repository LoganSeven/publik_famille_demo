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

from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.utils.misc import get_fk_model

from . import app_settings, utils


def update_ou_admin_roles(ou):
    if app_settings.MANAGED_CONTENT_TYPES == ():
        Role.objects.filter(slug=f'a2-managers-of-{ou.slug}').delete()
    else:
        ou_admin_role = ou.get_admin_role()

    for key, info in MANAGED_CT.items():
        ct = ContentType.objects.get_by_natural_key(key[0], key[1])
        model_class = ct.model_class()
        ou_model = get_fk_model(model_class, 'ou')
        # do not create scoped admin roles if the model is not scopable
        if not ou_model:
            continue
        name = str(info['name'])
        slug = '_a2-' + slugify(name)
        scoped_name = str(info['scoped_name'])
        name = scoped_name.format(ou=ou)
        ou_slug = slug + '-' + ou.slug
        if app_settings.MANAGED_CONTENT_TYPES == ():
            Role.objects.filter(slug=ou_slug, ou=ou).delete()
            continue
        ou_ct_admin_role = Role.objects.get_admin_role(
            instance=ct, ou=ou, name=name, slug=ou_slug, update_slug=True, update_name=True
        )
        if not app_settings.MANAGED_CONTENT_TYPES or key in app_settings.MANAGED_CONTENT_TYPES:
            ou_ct_admin_role.add_child(ou_admin_role)
        else:
            ou_ct_admin_role.remove_child(ou_admin_role)
        if info.get('must_search_user'):
            ou_ct_admin_role.permissions.add(utils.get_search_user_perm(ou))
        ou_ct_admin_role.permissions.add(utils.get_search_ou_perm(ou))


def update_ous_admin_roles():
    """Create general admin roles linked to all organizational units,
    they give general administrative rights to all mamanged content types
    scoped to the given organizational unit.
    """
    ou_all = OrganizationalUnit.objects.all()
    if len(ou_all) < 2:
        # If there is no ou or less than two, only generate global management
        # roles
        return
    for ou in ou_all:
        update_ou_admin_roles(ou)


MANAGED_CT = {
    ('a2_rbac', 'role'): {
        'name': _('Manager of roles'),
        'scoped_name': _('Roles - {ou}'),
        'must_search_user': True,
    },
    ('a2_rbac', 'organizationalunit'): {
        'name': _('Manager of organizational units'),
        'scoped_name': _('Organizational unit - {ou}'),
    },
    ('custom_user', 'user'): {
        'name': _('Manager of users'),
        'scoped_name': _('Users - {ou}'),
        'must_manage_authorizations_user': True,
    },
    ('authentic2', 'service'): {
        'name': _('Manager of services'),
        'scoped_name': _('Services - {ou}'),
    },
    ('authenticators', 'baseauthenticator'): {
        'name': _('Manager of authenticators'),
        'scoped_name': _('Authenticators - {ou}'),
    },
    ('authentic2', 'apiclient'): {
        'name': _('Manager of API clients'),
        'scoped_name': _('API clients - {ou}'),
    },
}


def update_content_types_roles():
    """Create general and scoped management roles for all managed content
    types.
    """
    cts = ContentType.objects.all()
    search_user_perm = utils.get_search_user_perm()
    search_ou_perm = utils.get_search_ou_perm()
    manage_authorizations_user_perm = utils.get_manage_authorizations_user_perm()
    slug = '_a2-manager'
    if app_settings.MANAGED_CONTENT_TYPES == ():
        Role.objects.filter(slug=slug).delete()
    else:
        admin_role, created = Role.objects.get_or_create(slug=slug, defaults=dict(name=gettext('Manager')))
        admin_role.add_self_administration()
        if not created and admin_role.name != gettext('Manager'):
            admin_role.name = gettext('Manager')
            admin_role.save()

    for ct in cts:
        ct_tuple = (ct.app_label.lower(), ct.model.lower())
        if ct_tuple not in MANAGED_CT:
            continue
        # General admin role
        name = str(MANAGED_CT[ct_tuple]['name'])
        slug = '_a2-' + slugify(name)
        if (
            app_settings.MANAGED_CONTENT_TYPES is not None
            and ct_tuple not in app_settings.MANAGED_CONTENT_TYPES
        ):
            Role.objects.filter(slug=slug).delete()
            continue
        ct_admin_role = Role.objects.get_admin_role(
            instance=ct, name=name, slug=slug, update_name=True, update_slug=True, create=True
        )
        if MANAGED_CT[ct_tuple].get('must_search_user'):
            ct_admin_role.permissions.add(search_user_perm)
        if MANAGED_CT[ct_tuple].get('must_manage_authorizations_user'):
            ct_admin_role.permissions.add(manage_authorizations_user_perm)
        ct_admin_role.permissions.add(search_ou_perm)
        ct_admin_role.add_child(admin_role)
