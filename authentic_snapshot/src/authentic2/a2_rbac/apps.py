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

from django.apps import AppConfig


class Authentic2RBACConfig(AppConfig):
    name = 'authentic2.a2_rbac'
    verbose_name = 'Authentic2 RBAC'

    def ready(self):
        from django.db.models.signals import post_delete, post_migrate, post_save

        from authentic2.models import Service

        from . import models, signal_handlers, signals

        # update role parenting when new role parenting is created
        post_save.connect(signal_handlers.role_parenting_post_save, sender=models.RoleParenting)
        # update role parenting when role parenting is deleted
        post_delete.connect(signal_handlers.role_parenting_post_delete, sender=models.RoleParenting)
        # or soft-created
        signals.post_soft_create.connect(
            signal_handlers.role_parenting_post_soft_delete, sender=models.RoleParenting
        )
        # or soft-deleted
        signals.post_soft_delete.connect(
            signal_handlers.role_parenting_post_soft_delete, sender=models.RoleParenting
        )
        # create CRUD operations and admin
        post_migrate.connect(signal_handlers.create_base_operations, sender=self)
        # update role parenting in post migrate
        post_migrate.connect(signal_handlers.fix_role_parenting_closure, sender=self)
        # update rbac on save to contenttype, ou and roles
        post_save.connect(signal_handlers.update_rbac_on_ou_post_save, sender=models.OrganizationalUnit)
        post_delete.connect(signal_handlers.update_rbac_on_ou_post_delete, sender=models.OrganizationalUnit)
        # keep service role and service ou field in sync
        for subclass in Service.__subclasses__():
            post_save.connect(signal_handlers.update_service_role_ou, sender=subclass)
        post_save.connect(signal_handlers.update_service_role_ou, sender=Service)
        post_migrate.connect(signal_handlers.create_default_ou, sender=self)
        post_migrate.connect(signal_handlers.create_default_permissions, sender=self)
        post_migrate.connect(signal_handlers.post_migrate_update_rbac, sender=self)
