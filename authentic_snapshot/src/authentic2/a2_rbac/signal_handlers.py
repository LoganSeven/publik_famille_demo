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

from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, router, transaction
from django.utils.translation import gettext as _
from django.utils.translation import override

from authentic2.a2_rbac.models import OrganizationalUnit, Role, RoleParenting
from authentic2.utils.misc import get_fk_model

from .managers import defer_update_transitive_closure
from .utils import get_operation


def create_default_ou(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    if not router.allow_migrate(using, OrganizationalUnit):
        return
    # be sure new objects names are localized using the default locale
    with override(settings.LANGUAGE_CODE):
        if OrganizationalUnit.objects.exists():
            return
        # Create a default OU if none exists currently
        default_ou, dummy = OrganizationalUnit.objects.get_or_create(
            slug='default',
            defaults={
                'default': True,
                'name': _('Default organizational unit'),
            },
        )
        # Update all existing models having an ou field to the default ou
        for app in apps.get_app_configs():
            for model in app.get_models():
                related_model = get_fk_model(model, 'ou')
                if not related_model == OrganizationalUnit:
                    return
                model.objects.filter(ou__isnull=True).update(ou=default_ou)


def post_migrate_update_rbac(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    # be sure new objects names are localized using the default locale
    from .management import update_content_types_roles, update_ous_admin_roles

    if not router.allow_migrate(using, Role):
        return
    with override(settings.LANGUAGE_CODE):
        with transaction.atomic():
            with defer_update_transitive_closure():
                update_content_types_roles()
                update_ous_admin_roles()


def update_rbac_on_ou_post_save(sender, instance, created, raw, **kwargs):
    from .management import update_ou_admin_roles, update_ous_admin_roles

    if OrganizationalUnit.objects.count() < 3 and created:
        update_ous_admin_roles()
    else:
        update_ou_admin_roles(instance)


def update_rbac_on_ou_post_delete(sender, instance, **kwargs):
    from .management import update_ous_admin_roles

    if OrganizationalUnit.objects.count() < 2:
        update_ous_admin_roles()


def update_service_role_ou(sender, instance, created, raw, **kwargs):
    Role.objects.filter(service=instance).update(ou=instance.ou)


def create_default_permissions(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    from .models import (
        ACTIVATE_OP,
        CHANGE_EMAIL_OP,
        CHANGE_PASSWORD_OP,
        MANAGE_AUTHORIZATIONS_OP,
        MANAGE_MEMBERS_OP,
        RESET_PASSWORD_OP,
    )

    if not router.allow_migrate(using, OrganizationalUnit):
        return

    with override(settings.LANGUAGE_CODE):
        get_operation(CHANGE_PASSWORD_OP)
        get_operation(RESET_PASSWORD_OP)
        get_operation(ACTIVATE_OP)
        get_operation(CHANGE_EMAIL_OP)
        get_operation(MANAGE_MEMBERS_OP)
        get_operation(MANAGE_AUTHORIZATIONS_OP)


def role_parenting_post_save(sender, instance, raw, created, **kwargs):
    '''Close the role parenting relation after instance creation'''
    if raw:  # do nothing if save comes from fixture loading
        return
    if not instance.direct:  # do nothing if instance is not direct
        return
    sender.objects.update_transitive_closure()


def role_parenting_post_delete(sender, instance, **kwargs):
    '''Close the role parenting relation after instance deletion'''
    if not instance.direct:  # do nothing if instance is not direct
        return
    sender.objects.update_transitive_closure()


def role_parenting_post_soft_delete(sender, instance, **kwargs):
    '''Close the role parenting relation after instance soft-deletion'''
    sender.objects.update_transitive_closure()


def create_base_operations(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    '''Create some basic operations, matching permissions from Django'''
    from . import models

    if not router.allow_migrate(using, models.Operation):
        return

    get_operation(models.ADD_OP)
    get_operation(models.CHANGE_OP)
    get_operation(models.DELETE_OP)
    get_operation(models.VIEW_OP)
    get_operation(models.ADMIN_OP)
    get_operation(models.SEARCH_OP)


def fix_role_parenting_closure(app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs):
    '''Close the role parenting relation after migrations'''
    if not router.allow_migrate(using, RoleParenting):
        return
    RoleParenting.objects.update_transitive_closure()
