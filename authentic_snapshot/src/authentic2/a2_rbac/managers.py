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

import contextlib
import datetime
import threading

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.db.models import query
from django.db.models.query import Prefetch, Q
from django.db.transaction import atomic

from authentic2.utils.postgres_utils import TrigramStrictWordDistance

from . import models as a2_models
from . import signals
from .utils import get_operation


class AbstractBaseManager(models.Manager):
    def get_by_natural_key(self, uuid):
        return self.get(uuid=uuid)


class OperationManager(models.Manager):
    def get_by_natural_key(self, slug):
        return self.get(slug=slug)

    def has_perm(self, user, operation_slug, object_or_model, ou=None):
        """Test if an user can do the operation given by operation_slug
        on the given object_or_model eventually scoped by an organizational
        unit given by ou.

        Returns True or False.
        """
        ou_query = query.Q(ou__isnull=True)
        if ou:
            ou_query |= query.Q(ou=ou.as_scope())
        ct = ContentType.objects.get_for_model(object_or_model)
        target_query = query.Q(target_ct=ContentType.objects.get_for_model(ContentType), target_id=ct.pk)
        if isinstance(object_or_model, models.Model):
            target_query |= query.Q(target_ct=ct, target_id=object.pk)
        qs = a2_models.Permission.objects.for_user(user)
        qs = qs.filter(operation__slug=operation_slug)
        qs = qs.filter(ou_query & target_query)
        return qs.exists()


class PermissionManagerBase(models.Manager):
    def get_by_natural_key(self, operation_slug, ou_nk, target_ct, target_nk):
        qs = self.filter(operation__slug=operation_slug)
        if ou_nk:
            try:
                ou = a2_models.OrganizationalUnit.objects.get_by_natural_key(*ou_nk)
            except a2_models.OrganizationalUnit.DoesNotExist:
                raise self.model.DoesNotExist
            qs = qs.filter(ou=ou)
        else:
            qs = qs.filter(ou__isnull=True)
        try:
            target_ct = ContentType.objects.get_by_natural_key(*target_ct)
        except ContentType.DoesNotExist:
            raise self.model.DoesNotExist
        target_model = target_ct.model_class()
        try:
            target = target_model.objects.get_by_natural_key(*target_nk)
        except target_model.DoesNotExist:
            raise self.model.DoesNotExist
        return qs.get(target_ct=ContentType.objects.get_for_model(target), target_id=target.pk)


class PermissionQueryset(query.QuerySet):
    def by_target_ct(self, target):
        """Filter permission whose target content-type matches the content
        type of the target argument
        """
        target_ct = ContentType.objects.get_for_model(target)
        return self.filter(target_ct=target_ct)

    def by_target(self, target):
        '''Filter permission whose target matches target'''
        return self.by_target_ct(target).filter(target_id=target.pk)

    def for_user(self, user):
        """Retrieve all permissions hold by an user through its role and
        inherited roles.
        """
        roles = a2_models.Role.objects.for_user(user=user)
        return self.filter(roles__in=roles)

    def cleanup(self):
        count = 0
        for p in self:
            if not p.target and (p.target_ct_id or p.target_id):
                p.delete()
                count += 1
        return count


PermissionManager = PermissionManagerBase.from_queryset(PermissionQueryset)


class IntCast(models.Func):
    function = 'int'
    template = 'CAST((%(expressions)s) AS %(function)s)'


class RoleQuerySet(query.QuerySet):
    def for_user(self, user):
        if hasattr(user, 'apiclient_roles'):
            queryset = self.filter(apiclients=user)
        else:
            queryset = self.filter(members=user)
        return queryset.parents().distinct()

    def parents(self, include_self=True, annotate=False, direct=None):
        assert annotate is False or direct is not True, 'annotate=True cannot be used with direct=True'
        if direct is None:
            qs = self.model.objects.filter(
                child_relation__deleted__isnull=True,
                child_relation__child__in=self,
            )
        else:
            qs = self.model.objects.filter(
                child_relation__deleted__isnull=True,
                child_relation__child__in=self,
                child_relation__direct=direct,
            )
        if include_self:
            qs = self | qs
        qs = qs.distinct()
        if annotate:
            qs = qs.annotate(direct=models.Max(IntCast('child_relation__direct')))
        return qs

    def children(self, include_self=True, annotate=False, direct=None):
        assert annotate is False or direct is not True, 'annotate=True cannot be used with direct=True'
        if direct is None:
            qs = self.model.objects.filter(
                parent_relation__deleted__isnull=True,
                parent_relation__parent__in=self,
            )
        else:
            qs = self.model.objects.filter(
                parent_relation__deleted__isnull=True,
                parent_relation__parent__in=self,
                parent_relation__direct=direct,
            )
        if include_self:
            qs = self | qs
        qs = qs.distinct()
        if annotate:
            qs = qs.annotate(direct=models.Max(IntCast('parent_relation__direct')))
        return qs

    def all_members(self):
        User = get_user_model()
        prefetch = Prefetch('roles', queryset=self, to_attr='direct')
        return (
            User.objects.filter(
                Q(roles__in=self)
                | Q(roles__parent_relation__parent__in=self, roles__parent_relation__deleted__isnull=True)
            )
            .distinct()
            .prefetch_related(prefetch)
        )

    def filter_admin_roles(self):
        return self.filter(slug__startswith='_a2-manager')

    def exclude_admin_roles(self):
        return self.exclude(slug__startswith='_a2-manager')

    def filter_internal_roles(self):
        return self.filter(slug__startswith='_')

    def exclude_internal_roles(self):
        return self.exclude(slug__startswith='_')

    def by_admin_scope_ct(self, admin_scope):
        admin_scope_ct = ContentType.objects.get_for_model(admin_scope)
        return self.filter(admin_scope_ct=admin_scope_ct)

    def filter_by_text_query(self, value, threshold=0.3):
        with connection.cursor() as cur:
            # default threshold is 0.5 (See
            # https://www.postgresql.org/docs/current/pgtrgm.html#PGTRGM-GUC)
            # lower it to 0.3 to be a little bit more permissive.
            cur.execute('SET pg_trgm.strict_word_similarity_threshold = %s', [threshold])
        qs = self.filter(name__trigram_strict_word_similar=value)
        qs = qs.annotate(dist=TrigramStrictWordDistance('name', value))
        qs = qs.order_by('dist')
        return qs

    def cleanup(self):
        count = 0
        for r in self.filter(Q(admin_scope_ct_id__isnull=False) | Q(admin_scope_id__isnull=False)):
            if not r.admin_scope:
                r.delete()
                count += 1
        return count


BaseRoleManager = AbstractBaseManager.from_queryset(RoleQuerySet)


class RoleParentingManager(models.Manager):
    class Local(threading.local):
        DO_UPDATE_CLOSURE = True
        CLOSURE_UPDATED = False

    tls = Local()

    def get_by_natural_key(self, parent_nk, child_nk, direct):
        try:
            parent = a2_models.Role.objects.get_by_natural_key(*parent_nk)
        except a2_models.Role.DoesNotExist:
            raise self.model.DoesNotExist
        try:
            child = a2_models.Role.objects.get_by_natural_key(*child_nk)
        except a2_models.Role.DoesNotExist:
            raise self.model.DoesNotExist
        return self.get(parent=parent, child=child, direct=direct)

    def soft_create(self, parent, child):
        with atomic(savepoint=False):
            rp, created = self.get_or_create(parent=parent, child=child, direct=True)
            new = created or rp.deleted
            if not created and rp.deleted:
                rp.created = datetime.datetime.now()
                rp.deleted = None
                rp.save(update_fields=['created', 'deleted'])
            if new:
                signals.post_soft_create.send(sender=self.model, instance=rp)

    def soft_delete(self, parent, child):
        qs = self.filter(parent=parent, child=child, deleted__isnull=True, direct=True)
        with atomic(savepoint=False):
            rp = qs.first()
            if rp:
                count = qs.update(deleted=datetime.datetime.now())
                # read-commited, view of tables can change during transaction
                if count:
                    signals.post_soft_delete.send(sender=self.model, instance=rp)

    def update_transitive_closure(self):
        """Recompute the transitive closure of the inheritance relation
        from scratch. Add missing indirect relations and delete
        obsolete indirect relations.
        """
        if not self.tls.DO_UPDATE_CLOSURE:
            self.tls.CLOSURE_UPDATED = True
            return

        with atomic(savepoint=False):
            # existing direct paths
            direct = set(self.filter(direct=True, deleted__isnull=True).values_list('parent_id', 'child_id'))
            old_indirects = set(
                self.filter(direct=False, deleted__isnull=True).values_list('parent_id', 'child_id')
            )
            indirects = set(direct)

            while True:
                changed = False
                for i, j in list(indirects):
                    for k, l in direct:
                        if j == k and i != l and (i, l) not in indirects:
                            indirects.add((i, l))
                            changed = True
                if not changed:
                    break

            with connection.cursor() as cur:
                # Delete old ones
                obsolete = old_indirects - indirects - direct
                if obsolete:
                    sql = '''UPDATE "%s" AS relation \
SET deleted = now()\
FROM (VALUES %s) AS dead(parent_id, child_id) \
WHERE relation.direct = 'false' AND relation.parent_id = dead.parent_id \
AND relation.child_id = dead.child_id AND deleted IS NULL''' % (
                        self.model._meta.db_table,
                        ', '.join('(%s, %s)' % (a, b) for a, b in obsolete),
                    )
                    cur.execute(sql)
                # Create new indirect relations
                new = indirects - old_indirects - direct
                if new:
                    new_values = ', '.join(
                        (
                            "(%s, %s, 'false', now(), NULL)" % (parent_id, child_id)
                            for parent_id, child_id in new
                        )
                    )
                    sql = '''INSERT INTO "%s" (parent_id, child_id, direct, created, deleted) VALUES %s \
ON CONFLICT (parent_id, child_id, direct) DO UPDATE SET created = EXCLUDED.created, deleted = NULL''' % (
                        self.model._meta.db_table,
                        new_values,
                    )
                    cur.execute(sql)


@contextlib.contextmanager
def defer_update_transitive_closure():
    RoleParentingManager.tls.DO_UPDATE_CLOSURE = False
    try:
        yield
        if RoleParentingManager.tls.CLOSURE_UPDATED:
            a2_models.RoleParenting.objects.update_transitive_closure()
    finally:
        RoleParentingManager.tls.DO_UPDATE_CLOSURE = True
        RoleParentingManager.tls.CLOSURE_UPDATED = False


class OrganizationalUnitManager(AbstractBaseManager):
    def get_by_natural_key(self, uuid):
        return self.get(slug=uuid)


class RoleManager(BaseRoleManager):
    def get_admin_role(
        self,
        instance,
        name,
        slug,
        *,
        ou=None,
        operation=None,
        update_name=False,
        update_slug=False,
        permissions=(),
        self_administered=False,
        create=True,
    ):
        '''Get or create the role of manager's of this object instance'''
        from .models import ADMIN_OP

        if operation is None:
            operation = ADMIN_OP

        kwargs = {}
        assert not ou or isinstance(
            instance, ContentType
        ), 'get_admin_role(ou=...) can only be used with ContentType instances: %s %s %s' % (
            name,
            ou,
            instance,
        )

        # Does the permission need to be scoped by ou ? Yes if the target is a
        # ContentType and ou is given. It's a general administration right upon
        # all instance of a ContentType, eventually scoped to the given ou.
        defaults = {}
        if isinstance(instance, ContentType):
            if ou:
                kwargs['ou'] = ou
            else:
                kwargs['ou__isnull'] = True
        else:  # for non ContentType instances, OU must be set to NULL, always.
            defaults['ou'] = None
        # find an operation matching the template
        op = get_operation(operation)
        if create:
            perm, _ = a2_models.Permission.objects.update_or_create(
                operation=op,
                target_ct=ContentType.objects.get_for_model(instance),
                target_id=instance.pk,
                defaults=defaults,
                **kwargs,
            )
        else:
            try:
                perm = a2_models.Permission.objects.get(
                    operation=op,
                    target_ct=ContentType.objects.get_for_model(instance),
                    target_id=instance.pk,
                    **kwargs,
                )
            except a2_models.Permission.DoesNotExist:
                return None

        # in which ou do we put the role ?
        if ou:
            mirror_role_ou = ou
        elif getattr(instance, 'ou', None):
            mirror_role_ou = instance.ou
        else:
            mirror_role_ou = None
        admin_role = self.get_mirror_role(
            perm,
            name,
            slug,
            ou=mirror_role_ou,
            update_name=update_name,
            update_slug=update_slug,
            create=create,
        )

        if not admin_role:
            return None

        permissions = set(permissions)
        permissions.add(perm)
        if self_administered:
            self_perm = admin_role.add_self_administration()
            permissions.add(self_perm)
        if set(admin_role.permissions.all()) != permissions:
            admin_role.permissions.set(permissions)
        return admin_role

    def get_mirror_role(
        self, instance, name, slug, ou=None, update_name=False, update_slug=False, create=True
    ):
        """Get or create a role which mirrors another model, for example a
        permission.
        """
        ct = ContentType.objects.get_for_model(instance)
        update_fields = {}
        kwargs = {}
        if ou:
            update_fields['ou'] = ou
        else:
            update_fields['ou'] = None
        if update_name:
            update_fields['name'] = name
        if update_slug:
            update_fields['slug'] = slug

        if create:
            role, _ = self.prefetch_related('permissions').update_or_create(
                admin_scope_ct=ct, admin_scope_id=instance.pk, defaults=update_fields, **kwargs
            )
        else:
            try:
                role = self.prefetch_related('permissions').get(
                    admin_scope_ct=ct, admin_scope_id=instance.pk, **kwargs
                )
            except self.model.DoesNotExist:
                return None
            for field, value in update_fields.items():
                setattr(role, field, value)
            role.save(update_fields=update_fields)
        return role

    def get_by_natural_key(self, slug, ou_natural_key, service_natural_key):
        kwargs = {'slug': slug}
        if ou_natural_key is None:
            kwargs['ou__isnull'] = True
        else:
            try:
                ou = a2_models.OrganizationalUnit.objects.get_by_natural_key(*ou_natural_key)
            except a2_models.OrganizationalUnit.DoesNotExist:
                raise self.model.DoesNotExist
            kwargs['ou'] = ou
        if service_natural_key is None:
            kwargs['service__isnull'] = True
        else:
            # XXX: prevent an import loop
            from authentic2.models import Service

            try:
                service = Service.objects.get_by_natural_key(*service_natural_key)
            except Service.DoesNotExist:
                raise self.model.DoesNotExist
            kwargs['service'] = service
        return self.get(**kwargs)
