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
import traceback

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.db.transaction import atomic
from django.utils.timezone import localtime

from authentic2 import app_settings
from authentic2.a2_rbac.models import ADMIN_OP
from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role
from authentic2.a2_rbac.utils import get_operation
from authentic2.custom_user.models import User

try:
    from authentic2.a2_rbac.models import MANAGE_MEMBERS_OP  # pylint: disable=C0412
except ImportError:
    MANAGE_MEMBERS_OP = None

MULTITENANT = 'hobo.multitenant' in settings.INSTALLED_APPS
if MULTITENANT:
    from hobo.multitenant.middleware import TenantMiddleware  # pylint: disable=import-error
    from tenant_schemas.utils import tenant_context  # pylint: disable=import-error


class FakeState:
    class FakeException(Exception):
        pass

    def __init__(self, faked=True):
        self.__faked = faked

    def dofake(self):
        self.__faked = True

    def dontfake(self):
        self.__faked = False

    def __bool__(self):
        return self.__faked

    def __nonzero__(self):
        return self.__faked


@contextlib.contextmanager
def fake_atomic(faked=True):
    faked = FakeState(faked=faked)
    try:
        with atomic():
            yield faked
            if faked:
                raise faked.FakeException
    except faked.FakeException:
        pass


class Command(BaseCommand):
    help = 'Check and repair authentic2 datas'

    def add_arguments(self, parser):
        parser.add_argument('--repair', action='store_true', help='repair what\'s broken', default=False)
        parser.add_argument('--noinput', action='store_true', help='do not ask questions', default=False)
        parser.add_argument('--fake', action='store_true', help='fake repair', default=False)
        if MULTITENANT:
            parser.add_argument('-d', '--domain', dest='domain_name', help='specify tenant domain name')
        for key in dir(self):
            if not key.startswith('check_'):
                continue
            if not hasattr(self.__class__, key):
                continue
            method = getattr(self, key)
            if callable(method) and method.__name__.startswith('check_'):
                slug = method.__name__.replace('_', '-')
                parser.add_argument(
                    '--no-%s' % slug,
                    action='store_false',
                    default=True,
                    dest=method.__name__,
                    help='disable check %s' % slug,
                )

    def handle(self, verbosity=0, repair=False, noinput=False, fake=False, domain_name=None, **options):
        self.verbosity = verbosity
        self.repair = repair
        self.noinput = noinput
        self.tenant = None
        self.tenant_shown = False
        self.fake = fake
        self.output = False

        if MULTITENANT and not getattr(getattr(connection, 'tenant', None), 'domain_url', None):
            for tenant in TenantMiddleware.get_tenants():
                if domain_name and tenant.domain_url != domain_name:
                    self.info('Skipping %s', tenant.domain_url)
                    continue
                self.tenant = tenant
                with tenant_context(tenant):
                    self.tenant_shown = False
                    self.output = False
                    with fake_atomic(faked=self.fake):
                        self.check_and_repair(options)
                    if self.fake and self.output:
                        self.success('Faked!')
        else:  # called in monotenant situation or with a tenant already set
            self.check_and_repair(options)

    def log(self, message, style, *args, **kwargs):
        self.output = True
        ending = kwargs.pop('ending', None)
        if self.tenant and not self.tenant_shown:
            self.tenant_shown = True
            self.stdout.write('== %s' % self.tenant.domain_url)
        message = message % (args or kwargs)
        if style:
            message = style(message)
        self.stdout.write(message, ending=ending)

    def error(self, message='', *args, **kwargs):
        self.log(message, self.style.ERROR, *args, **kwargs)

    def warning(self, message='', *args, **kwargs):
        if self.verbosity > 0:
            self.log(message, self.style.WARNING, *args, **kwargs)

    def notice(self, message='', *args, **kwargs):
        self.log(message, None, *args, **kwargs)

    def success(self, message='', *args, **kwargs):
        self.log(message, self.style.SUCCESS, *args, **kwargs)

    def info(self, message='', *args, **kwargs):
        if self.verbosity > 1:
            self.log(message, None, *args, **kwargs)

    @atomic
    def check_and_repair(self, options):
        for method in [
            self.check_roles_with_lost_admin_scope,
            self.check_duplicate_manage_members_permissions,
            self.check_duplicate_permissions,
            self.check_unused_permissions,
            self.check_instance_permission_ou,
            self.check_admin_roles_ou,
            self.check_manager_of_roles,
            self.check_identifiers_uniqueness,
        ]:
            if not options[method.__name__]:
                continue
            try:
                method()
            except Exception as e:
                self.error('Error during %s: %s', method, e)
                self.error('%s', traceback.format_exc(5))

    def do_repair(self):
        if self.repair:
            if self.noinput:
                return True
            self.notice('Repair (Y/N) [default=N] ?', ending=' ')
            while True:
                answer = input()
                if answer.lower() == 'y':
                    return True
                if not answer or answer.lower() == 'n':
                    return False

    def check_roles_with_lost_admin_scope(self):
        for role in Role.objects.filter(admin_scope_ct__isnull=False):
            try:
                role.admin_scope
            except ObjectDoesNotExist:
                self.warning('Role %s has lost its admin_scope', role)

    def check_unused_permissions(self):
        permission_ct = ContentType.objects.get_for_model(Permission)

        used_permission_ids = set()
        used_permission_ids.update(
            Role.objects.filter(admin_scope_ct=permission_ct).values_list('admin_scope_id', flat=True)
        )
        used_permission_ids.update(Role.permissions.through.objects.values_list('permission_id', flat=True))

        qs = Permission.objects.exclude(id__in=used_permission_ids)
        count = qs.count()

        if not count:
            return

        self.warning('Found %d unused permissions', count)
        if self.repair:
            for permission in qs:
                self.notice('- %s', permission)
        if self.do_repair():
            self.notice('Deleting unused permissions...', ending=' ')
            qs.delete()
            self.success('DONE!')

    def check_duplicate_manage_members_permissions(self):
        ct_ct = ContentType.objects.get_for_model(ContentType)
        permission_ct = ContentType.objects.get_for_model(Permission)
        manage_members_op = get_operation(MANAGE_MEMBERS_OP)
        permissions = Permission.objects.exclude(target_ct=ct_ct).filter(operation=manage_members_op)
        targets_with_duplicates = set(
            permissions.values_list('operation_id', 'target_ct_id', 'target_id')
            .annotate(count=Count(('operation_id', 'target_ct_id', 'target_id')))
            .filter(count__gt=1)
            .values_list('operation_id', 'target_ct_id', 'target_id')
        )
        if targets_with_duplicates:
            self.warning('Found %d manage members permissions with duplicates', len(targets_with_duplicates))
            if self.repair:
                for dummy_operation_id, target_ct_id, target_id in targets_with_duplicates:
                    qs = Permission.objects.filter(target_ct_id=target_ct_id, target_id=target_id)
                    for perm in qs:
                        linked_admin_role = Role.objects.get(
                            admin_scope_ct=permission_ct, admin_scope_id=perm.id
                        )
                    target = (
                        ContentType.objects.get_for_id(target_ct_id).model_class().objects.get(pk=target_id)
                    )
                    self.notice(' - %s: [%s]', target, '; '.join(map(str, qs)))
            if self.do_repair():
                self.notice('Deleting duplicate manage members permissions...', ending=' ')
                for dummy_operation_id, target_ct_id, target_id in targets_with_duplicates:
                    qs = Permission.objects.filter(target_ct_id=target_ct_id, target_id=target_id).order_by(
                        'id'
                    )
                    role_perms = []
                    for perm in qs:
                        linked_admin_role = Role.objects.filter(
                            admin_scope_ct=permission_ct, admin_scope_id=perm.id
                        ).first()
                        if linked_admin_role:
                            role_perms.append((perm, linked_admin_role))
                        else:
                            perm.delete()
                    if role_perms:
                        first_role = role_perms[0][1]
                        user_ids = set()
                        user_ids.update(first_role.all_members().values_list('id', flat=True))
                        for perm, role in role_perms[1:]:
                            user_ids.update(role.all_members().values_list('id', flat=True))
                            first_role.members.add(*role.members.all())
                            for child_role in role.children(include_self=False):
                                first_role.add_child(child_role)
                            role.delete()
                            perm.delete()
                        assert first_role.all_members().distinct().count() == len(user_ids)
                self.success('DONE!')

    def check_duplicate_permissions(self):
        ct_ct = ContentType.objects.get_for_model(ContentType)
        permissions = Permission.objects.exclude(target_ct=ct_ct)
        targets_with_duplicates = set(
            permissions.values_list('operation_id', 'target_ct_id', 'target_id')
            .annotate(count=Count(('operation_id', 'target_ct_id', 'target_id')))
            .filter(count__gt=1)
            .values_list('operation_id', 'target_ct_id', 'target_id')
        )
        if targets_with_duplicates:
            self.warning('Found %d instance permissions with duplicates', len(targets_with_duplicates))
            if self.repair:
                for operation_id, target_ct_id, target_id in targets_with_duplicates:
                    qs = Permission.objects.filter(
                        operation_id=operation_id, target_ct_id=target_ct_id, target_id=target_id
                    )
                    target = (
                        ContentType.objects.get_for_id(target_ct_id).model_class().objects.get(pk=target_id)
                    )
                    self.notice(' - %s: [%s]', target, '; '.join(map(str, qs)))
            if self.do_repair():
                self.notice('Deleting duplicate permissions...', ending=' ')
                for operation_id, target_ct_id, target_id in targets_with_duplicates:
                    qs = list(
                        Permission.objects.filter(
                            operation_id=operation_id, target_ct_id=target_ct_id, target_id=target_id
                        ).order_by('id')
                    )
                    first_perm = qs[0]
                    for perm in qs[1:]:
                        perm.delete()
                    if first_perm.ou:
                        first_perm.ou = None
                        first_perm.save()
                self.success('DONE!')

    def check_instance_permission_ou(self):
        ct_ct = ContentType.objects.get_for_model(ContentType)
        permissions = Permission.objects.exclude(target_ct=ct_ct).filter(ou__isnull=False)
        count = permissions.count()
        if count:
            self.warning('Found %d instance permissions with an ou.', count)
            if self.do_repair():
                self.notice('Changing ou of instance permissions...', ending=' ')
                permissions.update(ou=None)
                self.success('DONE!')

    def check_admin_roles_ou(self):
        permission_ct = ContentType.objects.get_for_model(Permission)
        role_ct = ContentType.objects.get_for_model(Role)
        manage_members_op = get_operation(MANAGE_MEMBERS_OP)
        roles_to_fix = {}
        for ou in OU.objects.all():
            roles = Role.objects.exclude(admin_scope_ct=permission_ct).filter(ou=ou)
            permissions = Permission.objects.filter(
                operation=manage_members_op, target_ct=role_ct, target_id__in=roles.values_list('id')
            )
            wrong_ou_roles = Role.objects.filter(
                admin_scope_ct=permission_ct, admin_scope_id__in=permissions.values_list('id')
            ).exclude(ou=ou)
            if wrong_ou_roles:
                self.warning('Found %4d admin role with wrong ou in ou %s', wrong_ou_roles.count(), ou)
                roles_to_fix[ou] = wrong_ou_roles
        if roles_to_fix and self.do_repair():
            self.notice('Changing ou of admin roles...', ending=' ')
            for ou, role in roles_to_fix.items():
                role.update(ou=ou)
            self.success('DONE!')

    def check_manager_of_roles(self):
        permission_ct = ContentType.objects.get_for_model(Permission)
        role_ct = ContentType.objects.get_for_model(Role)

        admin_op = get_operation(ADMIN_OP)
        operations = [admin_op]
        if MANAGE_MEMBERS_OP:
            manage_members_op = get_operation(MANAGE_MEMBERS_OP)
            operations.append(manage_members_op)

        roles = Role.objects.exclude(slug__startswith='_a2-managers-of-role-')

        for role in roles:
            manager_perms = Permission.objects.filter(
                operation__in=operations, target_ct=role_ct, target_id=role.id
            )
            manager_perms_ids = manager_perms.values_list('id', flat=True)
            manager_roles = Role.objects.filter(
                slug__startswith='_a2-managers-of-role-',
                admin_scope_ct=permission_ct,
                admin_scope_id__in=manager_perms_ids,
            )
            role_shown = False
            to_delete = []
            to_change_ou = []
            with fake_atomic() as fake_state:
                admin_role = role.get_admin_role()
                ok = set(manager_roles) <= {admin_role}
                if ok and manager_roles:
                    if list(manager_roles)[0].ou != role.ou:
                        self.warning(
                            '- "%s" detected wrong ou, should be "%s" and is "%s"',
                            admin_role,
                            role.ou,
                            admin_role.ou,
                        )
                        to_change_ou.append((admin_role, role.ou))
                    continue
                add_members = set()
                add_children = set()
                for manager_role in manager_roles:
                    if manager_role == admin_role:
                        continue
                    members_count = manager_role.all_members().count()
                    direct_members = manager_role.members.all()
                    direct_members_count = direct_members.count()
                    direct_children = Role.objects.filter(
                        parent_relation__deleted__isnull=True,
                        parent_relation__parent=manager_role,
                        parent_relation__direct=True,
                    )
                    direct_children_count = direct_children.count()
                    show = members_count or self.verbosity > 1
                    if show:
                        if not role_shown:
                            role_shown = True
                            self.notice('- "%s" has problematic manager roles', role)
                        self.warning('  - %s', manager_role, ending=' ')
                    direct_parents = Role.objects.filter(
                        child_relation__deleted__isnull=True,
                        child_relation__child=manager_role,
                        child_relation__direct=True,
                    )
                    if show:
                        self.warning('DELETE', ending=' ')
                    to_delete.append(manager_role)
                    if manager_role.ou != role.ou:
                        if show:
                            self.warning('WRONG_OU', ending=' ')
                    if manager_role.admin_scope.ou != role.ou:
                        if show:
                            self.warning('WRONG_PERMISSION_OU', ending=' ')
                    if MANAGE_MEMBERS_OP and manager_role.admin_scope.operation == admin_op:
                        if show:
                            self.warning('WRONG_PERMISSION_OPERATION', ending=' ')
                    if direct_members_count:
                        if show:
                            self.warning('MEMBERS(%d)', direct_members_count, ending=' ')
                        add_members.update(direct_members)
                    if (members_count - direct_children_count) and show:
                        self.warning('INDIRECT_MEMBERS(%d)', members_count - direct_members_count, ending=' ')
                    if direct_children_count:
                        if show:
                            self.warning('CHILDREN(%d)', direct_children_count, ending=' ')
                        add_children.update(direct_children)
                    if show:
                        self.notice('')
                    if direct_parents.exists() and show:
                        self.error('     SHOULD NOT HAVE PARENTS')
                        for parent in direct_parents:
                            self.error('     - %s(id=%s)', parent, parent.id)
                repair = (self.repair and self.noinput) or (self.repair and self.do_repair())
                if repair:
                    if add_children or add_members:
                        fake_state.dontfake()
                        if add_members:
                            admin_role.members.add(*add_members)
                        add_members = None
                        if add_children:
                            for child in add_children:
                                admin_role.add_child(child)
                            add_children = None
            if repair:
                for role_to_delete in to_delete:
                    role_to_delete.delete()
                for admin_role, ou in to_change_ou:
                    admin_role.ou = ou
                    admin_role.save(update_fields=['ou'])
            if role_shown:
                self.notice('')
            self.stdout.flush()

        for admin_role in Role.objects.filter(slug__startswith='_a2-managers-of-role-'):
            if not admin_role.admin_scope:
                self.warning('invalid admin role "%s": no admin scope', admin_role)
            admin_permissions = (
                admin_role.permissions.filter(operation__in=operations, target_ct=role_ct)
                .select_related('ou')
                .prefetch_related('target__ou')
            )
            count = admin_permissions.count()
            if not count:
                self.warning('invalid admin role "%s" no admin permission', admin_role)
            elif count != 2:
                self.warning('invalid admin role "%s" too few or too many admin permissions', admin_role)
                for admin_permission in admin_permissions:
                    self.notice(' - %s', admin_permission)
            for admin_permission in admin_permissions:
                if MANAGE_MEMBERS_OP and admin_permission.operation != manage_members_op:
                    self.warning(
                        'invalid admin role "%s" invalid permission "%s": not manage_members operation',
                        admin_role,
                        admin_permission,
                    )
                if not (
                    (admin_permission.target != admin_role and admin_permission == admin_role.admin_scope)
                    or (admin_permission.target == admin_role)
                ):
                    self.warning(
                        'invalid admin role "%s" invalid permission "%s": not admin_scope and not self manage'
                        ' permission',
                        admin_role,
                        admin_permission,
                    )
                if admin_permission.ou is not None:
                    self.warning(
                        'invalid admin role "%s" invalid permission "%s": wrong ou "%s"',
                        admin_role,
                        admin_permission,
                        admin_permission.ou,
                    )
                    admin_permission.target.get_admin_role()
                if admin_permission.target.ou != admin_role.ou:
                    self.warning(
                        'invalid admin role "%s" wrong ou, should be "%s" is "%s"',
                        admin_role,
                        admin_permission.target.ou,
                        admin_role.ou,
                    )

    def duplicate_emails(self, user_qs):
        return (
            user_qs.order_by()
            .values(iemail=Lower('email'))
            .annotate(count_id=Count('id'))
            .filter(count_id__gt=1)
            .values_list('iemail', flat=True)
            .exclude(iemail='')
        )

    def duplicate_username(self, user_qs):
        return (
            user_qs.order_by()
            .values('username')
            .exclude(Q(username__isnull=True) | Q(username=''))
            .annotate(count_id=Count('id'))
            .filter(count_id__gt=1)
            .values_list('username', flat=True)
        )

    def check_identifiers_uniqueness(self):
        users = User.objects.prefetch_related('userexternalid_set')
        if app_settings.A2_USERNAME_IS_UNIQUE:
            self._check_username_uniqueness(users, 'Username should be globally unique')
        for ou in OU.objects.all():
            ou_users = users.filter(ou=ou)
            if ou.email_is_unique:
                self._check_email_uniqueness(ou_users, 'Email should be unique in ou "%s"' % ou)
            if ou.username_is_unique:
                self._check_username_uniqueness(ou_users, 'Username should be unique in ou "%s"' % ou)

    def _check_email_uniqueness(self, qs, msg):
        emails = self.duplicate_emails(qs)
        if not emails:
            return
        users = qs.annotate(iemail=Lower('email'))
        count = users.filter(iemail__in=emails).count()
        self.warning('%s, found %%d user accounts with same email:' % msg, count)
        for email in emails:
            self.notice('- %s :', email)
            self.show_users(users.filter(iemail=email))

    def show_users(self, users):
        for user in users:
            self.notice('  * "%s" %s ', user, user, ending=' ')
            self.notice('(created %s', localtime(user.date_joined).strftime('%Y-%m-%dT%H:%M:%S'), ending=' ')
            if user.last_login:
                self.notice(
                    ', last login %s', localtime(user.last_login).strftime('%Y-%m-%dT%H:%M:%S'), ending=''
                )
            else:
                self.notice(', never logged in', ending='')
            external_ids = list(user.userexternalid_set.all())
            if external_ids:
                self.notice(
                    ', external-ids: %s',
                    ', '.join(
                        external_id.source + '#' + external_id.external_id for external_id in external_ids
                    ),
                    ending='',
                )
            self.notice(')')

    def _check_username_uniqueness(self, qs, msg):
        usernames = self.duplicate_username(qs)
        if not usernames:
            return
        users = qs.filter(username__in=usernames)
        self.warning('%s, found %%d user accounts with same username:' % msg, users.count())
        for username in usernames:
            self.notice('- %s :', username)
            self.show_users(qs.filter(username=username))
