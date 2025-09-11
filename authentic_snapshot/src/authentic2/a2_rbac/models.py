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

import hashlib
import os
from collections import namedtuple

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres import indexes as postgresql_indexes
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.db.models.query import Prefetch, Q
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from model_utils.managers import QueryManager

from authentic2.decorators import errorcollector
from authentic2.utils.cache import GlobalCache
from authentic2.validators import HexaColourValidator

from . import fields, managers, utils


class AbstractBase(models.Model):
    """Abstract base model for all models having a name and uuid and a
    slug
    """

    uuid = models.CharField(max_length=32, verbose_name=_('uuid'), unique=True, default=utils.get_hex_uuid)
    name = models.CharField(max_length=256, verbose_name=_('name'))
    slug = models.SlugField(max_length=256, verbose_name=_('slug'))
    description = models.TextField(verbose_name=_('description'), blank=True)

    objects = managers.AbstractBaseManager()

    def __str__(self):
        return str(self.name)

    def __repr__(self):
        return f'<{self.__class__.__name__} {repr(self.slug)} {repr(self.name)}>'

    def save(self, *args, **kwargs):
        # truncate slug and add a hash if it's too long
        if not self.slug:
            self.slug = utils.generate_slug(self.name)
        if len(self.slug) > 256:
            self.slug = self.slug[:252] + hashlib.md5(self.slug.encode()).hexdigest()[:4]
        if not self.uuid:
            self.uuid = utils.get_hex_uuid()
        return super().save(*args, **kwargs)

    def natural_key(self):
        return [self.uuid]

    class Meta:
        abstract = True


class OrganizationalUnit(AbstractBase):
    RESET_LINK_POLICY = 0
    MANUAL_PASSWORD_POLICY = 1

    USER_ADD_PASSWD_POLICY_CHOICES = (
        (RESET_LINK_POLICY, _('Send reset link')),
        (MANUAL_PASSWORD_POLICY, _('Manual password definition')),
    )

    USER_CAN_RESET_PASSWD_CHOICES = (
        (None, _('System default')),
        (True, _('Yes')),
        (False, _('No')),
    )

    PolicyValue = namedtuple(
        'PolicyValue',
        ['generate_password', 'reset_password_at_next_login', 'send_mail', 'send_password_reset'],
    )

    USER_ADD_PASSWD_POLICY_VALUES = {
        RESET_LINK_POLICY: PolicyValue(False, False, False, True),
        MANUAL_PASSWORD_POLICY: PolicyValue(False, False, True, False),
    }

    username_is_unique = models.BooleanField(blank=True, default=False, verbose_name=_('Username is unique'))
    email_is_unique = models.BooleanField(blank=True, default=False, verbose_name=_('Email is unique'))
    phone_is_unique = models.BooleanField(blank=True, default=False, verbose_name=_('Phone is unique'))
    default = fields.UniqueBooleanField(verbose_name=_('Default organizational unit'))

    validate_emails = models.BooleanField(
        blank=True,
        default=False,
        verbose_name=_('Validate emails when modified in backoffice'),
        help_text=_(
            "If checked, an agent in backoffice won't be able to directly edit the user's "
            'email address, instead a confirmation link will be sent to the newly-declared '
            'address for the change to be effective.'
        ),
    )

    show_username = models.BooleanField(blank=True, default=True, verbose_name=_('Show username'))

    check_required_on_login_attributes = models.BooleanField(
        blank=True, default=True, verbose_name=_('Check required on login attributes')
    )

    admin_perms = GenericRelation('Permission', content_type_field='target_ct', object_id_field='target_id')

    user_can_reset_password = models.BooleanField(
        verbose_name=_('Users can reset password'),
        choices=USER_CAN_RESET_PASSWD_CHOICES,
        null=True,
        default=None,
        blank=True,
    )

    user_add_password_policy = models.IntegerField(
        verbose_name=_('User creation password policy'), choices=USER_ADD_PASSWD_POLICY_CHOICES, default=0
    )

    clean_unused_accounts_alert = models.PositiveIntegerField(
        verbose_name=_('Days after which the user receives an account deletion alert'),
        validators=[
            MinValueValidator(
                30, _('Ensure that this value is greater than 30 days, or leave blank for deactivating.')
            )
        ],
        null=True,
        blank=True,
        default=730,  # a month before the deletion deadline = two years
    )

    clean_unused_accounts_deletion = models.PositiveIntegerField(
        verbose_name=_('Delay in days before cleaning unused accounts'),
        validators=[
            MinValueValidator(
                30, _('Ensure that this value is greater than 30 days, or leave blank for deactivating.')
            )
        ],
        null=True,
        blank=True,
        default=760,  # two years + 1 month
    )
    home_url = models.URLField(verbose_name=_('Home URL'), max_length=256, null=True, blank=True)
    logo = models.ImageField(verbose_name=_('Logo'), blank=True, upload_to='services/logos')
    colour = models.CharField(
        verbose_name=_('Colour'), null=True, blank=True, max_length=32, validators=[HexaColourValidator()]
    )

    objects = managers.OrganizationalUnitManager()

    class Meta:
        verbose_name = _('organizational unit')
        verbose_name_plural = _('organizational units')
        ordering = ('name',)
        unique_together = (
            ('name',),
            ('slug',),
        )

    def as_scope(self):
        return self

    def clean(self):
        # if we set this ou as the default one, we must unset the other one if
        # there is
        if self.default:
            qs = self.__class__.objects.filter(default=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            qs.update(default=None)
        if self.pk and not self.default and self.__class__.objects.get(pk=self.pk).default:
            raise ValidationError(
                _(
                    'You cannot unset this organizational unit as the default, but you can set another one as'
                    ' the default.'
                )
            )
        if bool(self.clean_unused_accounts_alert) ^ bool(self.clean_unused_accounts_deletion):
            raise ValidationError(_('Deletion and alert delays must be set together.'))
        if (
            self.clean_unused_accounts_alert
            and self.clean_unused_accounts_alert >= self.clean_unused_accounts_deletion
        ):
            raise ValidationError(_('Deletion alert delay must be less than actual deletion delay.'))
        super().clean()

    def get_admin_role(self):
        """Get or create the generic admin role for this organizational
        unit.
        """
        name = _('Managers of "{ou}"').format(ou=self)
        slug = f'_a2-managers-of-{self.slug}'
        return Role.objects.get_admin_role(
            instance=self,
            name=name,
            slug=slug,
            operation=VIEW_OP,
            update_name=True,
            update_slug=True,
            create=True,
        )

    def delete(self, *args, **kwargs):
        if self.logo and os.path.exists(self.logo.path):
            os.unlink(self.logo.path)

        Permission.objects.filter(ou=self).delete()
        return super().delete(*args, **kwargs)

    def natural_key(self):
        return [self.slug]

    @classmethod
    @GlobalCache(timeout=5)
    def cached(cls):
        return cls.objects.all()

    def export_json(self):
        return {
            'uuid': self.uuid,
            'slug': self.slug,
            'name': self.name,
            'description': self.description,
            'default': self.default,
            'email_is_unique': self.email_is_unique,
            'phone_is_unique': self.phone_is_unique,
            'username_is_unique': self.username_is_unique,
            'validate_emails': self.validate_emails,
        }

    def __str__(self):
        return str(self.name)

    def get_absolute_url(self):
        return reverse('a2-manager-ou-detail', kwargs={'pk': self.pk})


OrganizationalUnit._meta.natural_key = [['uuid'], ['slug'], ['name']]


class Permission(models.Model):
    operation = models.ForeignKey(
        to='a2_rbac.Operation', verbose_name=_('operation'), on_delete=models.CASCADE
    )
    ou = models.ForeignKey(
        to=OrganizationalUnit,
        verbose_name=_('organizational unit'),
        related_name='scoped_permission',
        null=True,
        on_delete=models.CASCADE,
    )
    target_ct = models.ForeignKey(to='contenttypes.ContentType', related_name='+', on_delete=models.CASCADE)
    target_id = models.PositiveIntegerField()
    target = GenericForeignKey('target_ct', 'target_id')

    objects = managers.PermissionManager()

    class Meta:
        verbose_name = _('permission')
        verbose_name_plural = _('permissions')
        constraints = [
            models.UniqueConstraint(
                fields=['operation', 'target_ct', 'target_id'],
                name='null_ou_uniq_idx',
                condition=models.Q(ou__isnull=True),
            ),
        ]

    mirror_roles = GenericRelation(
        'Role',
        content_type_field='admin_scope_ct',
        object_id_field='admin_scope_id',
    )

    def natural_key(self):
        return [
            self.operation.slug,
            self.ou and self.ou.natural_key(),
            self.target and self.target_ct.natural_key(),
            self.target and self.target.natural_key(),
        ]

    def export_json(self):
        return {
            'operation': self.operation.natural_key_json(),
            'ou': self.ou and self.ou.natural_key_json(),
            'target_ct': self.target_ct.natural_key_json(),
            'target': self.target.natural_key_json(),
        }

    @classmethod
    def from_str(cls, s, instance=None):
        '''Build permission from a string of the form [ou_slug? app_label.model_operation].

        The optional ou_slug is used to created OU scoped permissions.
        An optional instance argument can be used to create a permission on an instance.
        '''
        ou_slug = None
        s = s.strip()
        if ' ' in s:
            ou_slug, permission = s.split()
        else:
            permission = s
        app_label, operation_model = permission.split('.', 1)
        operation, model = operation_model.split('_')
        app = apps.get_app_config(app_label)
        model_class = app.get_model(model)

        if instance is None:
            permission, _ = Permission.objects.get_or_create(
                operation=Operation.objects.get(slug=operation),
                ou=OrganizationalUnit.objects.get(slug=ou_slug) if ou_slug else None,
                target_ct=ContentType.objects.get_for_model(ContentType),
                target_id=ContentType.objects.get_for_model(model_class).pk,
            )
        else:
            assert isinstance(instance, model_class), f'{instance} is not an instance of {model_class}'
            permission, _ = Permission.objects.get_or_create(
                operation=Operation.objects.get(slug=operation),
                ou=OrganizationalUnit.objects.get(slug=ou_slug) if ou_slug else None,
                target_ct=ContentType.objects.get_for_model(instance),
                target_id=instance.pk,
            )
        return permission

    def __str__(self):
        ct = ContentType.objects.get_for_id(self.target_ct_id)
        ct_ct = ContentType.objects.get_for_model(ContentType)
        if ct == ct_ct:
            target = ContentType.objects.get_for_id(self.target_id)
            s = f'{self.operation} / {target}'
        else:
            s = f'{self.operation} / {ct.name} / {self.target}'
        if self.ou:
            s += gettext(' (scope "{0}")').format(self.ou)
        return s


Permission._meta.natural_key = [
    ['operation', 'ou', 'target'],
    ['operation', 'ou__isnull', 'target'],
]


class Role(AbstractBase):
    ou = models.ForeignKey(
        to=OrganizationalUnit,
        verbose_name=_('organizational unit'),
        swappable=True,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )
    members = models.ManyToManyField(
        to=settings.AUTH_USER_MODEL, swappable=True, blank=True, related_name='roles'
    )
    permissions = models.ManyToManyField(to=Permission, related_name='roles', blank=True)
    name = models.TextField(verbose_name=_('name'))
    details = models.TextField(_('Role details (frontoffice)'), blank=True)
    emails = ArrayField(models.EmailField(), default=list)
    emails_to_members = models.BooleanField(_('Emails to members'), default=True)
    is_superuser = models.BooleanField(default=False)
    admin_scope_ct = models.ForeignKey(
        to='contenttypes.ContentType',
        null=True,
        blank=True,
        verbose_name=_('administrative scope content type'),
        on_delete=models.CASCADE,
    )
    admin_scope_id = models.PositiveIntegerField(
        verbose_name=_('administrative scope id'), null=True, blank=True
    )
    admin_scope = GenericForeignKey('admin_scope_ct', 'admin_scope_id')
    service = models.ForeignKey(
        to='authentic2.Service',
        verbose_name=_('service'),
        null=True,
        blank=True,
        related_name='roles',
        on_delete=models.CASCADE,
    )
    external_id = models.TextField(verbose_name=_('external id'), blank=True, db_index=True)

    admin_perms = GenericRelation('Permission', content_type_field='target_ct', object_id_field='target_id')

    can_manage_members = models.BooleanField(
        default=True, verbose_name=_('Allow adding or deleting role members')
    )

    objects = managers.RoleQuerySet.as_manager()

    def add_child(self, child):
        RoleParenting.objects.soft_create(self, child)

    def remove_child(self, child):
        RoleParenting.objects.soft_delete(self, child)

    def add_parent(self, parent):
        RoleParenting.objects.soft_create(parent, self)

    def remove_parent(self, parent):
        RoleParenting.objects.soft_delete(parent, self)

    def parents(self, include_self=True, annotate=False, direct=None):
        return self.__class__.objects.filter(pk=self.pk).parents(
            include_self=include_self, annotate=annotate, direct=direct
        )

    def children(self, include_self=True, annotate=False, direct=None):
        return self.__class__.objects.filter(pk=self.pk).children(
            include_self=include_self,
            annotate=annotate,
            direct=direct,
        )

    def all_members(self):
        User = get_user_model()
        prefetch = Prefetch('roles', queryset=self.__class__.objects.filter(pk=self.pk), to_attr='direct')

        return (
            User.objects.filter(
                Q(roles=self)
                | Q(roles__parent_relation__parent=self) & Q(roles__parent_relation__deleted__isnull=True)
            )
            .distinct()
            .prefetch_related(prefetch)
        )

    def is_direct(self):
        if hasattr(self, 'direct'):
            if self.direct is None:
                return True
            return bool(self.direct)
        return None

    def get_admin_role(self, create=True):
        from . import utils

        search_user_perm = utils.get_search_user_perm(ou=self.ou)
        admin_role = self.__class__.objects.get_admin_role(
            self,
            name=_('Managers of role "{role}"').format(role=str(self)),
            slug=f'_a2-managers-of-role-{slugify(str(self))}',
            permissions=(search_user_perm,),
            self_administered=True,
            update_name=True,
            update_slug=True,
            create=create,
            operation=MANAGE_MEMBERS_OP,
        )
        return admin_role

    def validate_unique(self, exclude=None):
        errors = {}

        with errorcollector(errors):
            super().validate_unique(exclude=exclude)

        exclude = exclude or []

        if 'name' not in exclude:
            qs = self.__class__.objects.filter(name=self.name, ou=self.ou)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors.setdefault('name', []).append(_('Name already used'))

        if 'slug' not in exclude:
            qs = self.__class__.objects.filter(slug=self.slug, ou=self.ou)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors.setdefault('slug', []).append(_('Slug already used'))

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Service roles can only be part of the same ou as the service
        if self.service:
            self.ou = self.service.ou
        result = super().save(*args, **kwargs)
        self.get_admin_role(create=False)
        return result

    def has_self_administration(self, op=None):
        if not op:
            op = MANAGE_MEMBERS_OP
        operation = utils.get_operation(op)
        self_perm, dummy = Permission.objects.get_or_create(
            operation=operation,
            target_ct=ContentType.objects.get_for_model(self),
            target_id=self.pk,
            ou__is_null=True,
        )
        return self.permissions.filter(pk=self_perm.pk).exists()

    def add_self_administration(self, op=None):
        'Add permission to role so that it is self-administered'
        if not op:
            op = MANAGE_MEMBERS_OP
        operation = utils.get_operation(op)
        self_perm, dummy = Permission.objects.get_or_create(
            operation=operation, target_ct=ContentType.objects.get_for_model(self), target_id=self.pk
        )
        self.permissions.through.objects.get_or_create(role=self, permission=self_perm)
        return self_perm

    def is_internal(self):
        return self.slug.startswith('_')

    def add_permission(self, model_or_instance, operation_tpl, ou=None):
        if isinstance(model_or_instance, models.Model):
            target_ct = ContentType.objects.get_for_model(model_or_instance)
            target_id = model_or_instance.pk
        elif isinstance(model_or_instance, type) and issubclass(model_or_instance, models.Model):
            target_ct = ContentType.objects.get_for_model(ContentType)
            target_id = ContentType.objects.get_for_model(model_or_instance).pk
        else:
            raise ValueError('invalid model_or_instance')
        if isinstance(operation_tpl, str):
            operation = Operation.objects.get(slug=operation_tpl)
        else:
            operation = utils.get_operation(operation_tpl)
        permission, _ = Permission.objects.get_or_create(
            operation=operation, target_ct=target_ct, target_id=target_id, ou=ou
        )
        self.permissions.add(permission)

    def remove_permission(self, model_or_instance, operation_tpl, ou=None):
        if isinstance(model_or_instance, models.Model):
            target_ct = ContentType.objects.get_for_model(model_or_instance)
            target_id = model_or_instance.pk
        elif isinstance(model_or_instance, type) and issubclass(model_or_instance, models.Model):
            target_ct = ContentType.objects.get_for_model(ContentType)
            target_id = ContentType.objects.get_for_model(model_or_instance).pk
        else:
            raise ValueError('invalid model_or_instance')
        if isinstance(operation_tpl, str):
            operation = Operation.objects.get(slug=operation_tpl)
        else:
            operation = utils.get_operation(operation_tpl)
        qs = Permission.objects.filter(target_ct=target_ct, target_id=target_id, operation=operation)
        if ou:
            qs = qs.filter(ou=ou)
        else:
            qs = qs.filter(ou__isnull=True)
        self.permissions.through.objects.filter(permission__in=qs).delete()

    objects = managers.RoleManager()

    class Meta:
        verbose_name = _('role')
        verbose_name_plural = _('roles')
        ordering = (
            'ou',
            'service',
            'name',
        )
        unique_together = [
            ('admin_scope_ct', 'admin_scope_id'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['ou', 'service', 'slug'],
                name='slug_uniq_idx',
                condition=models.Q(admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['ou', 'service', 'name'],
                name='name_uniq_idx',
                condition=models.Q(admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['ou', 'slug'],
                name='null_service_slug_uniq_idx',
                condition=models.Q(service__isnull=True, admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['service', 'slug'],
                name='null_ou_slug_uniq_idx',
                condition=models.Q(ou__isnull=True, admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['slug'],
                name='null_ou_service_slug_uniq_idx',
                condition=models.Q(service__isnull=True, ou__isnull=True, admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['ou', 'name'],
                name='null_service_name_uniq_idx',
                condition=models.Q(service__isnull=True, admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['service', 'name'],
                name='null_ou_name_uniq_idx',
                condition=models.Q(ou__isnull=True, admin_scope_ct__isnull=True),
            ),
            models.UniqueConstraint(
                fields=['name'],
                name='null_ou_service_name_uniq_idx',
                condition=models.Q(service__isnull=True, ou__isnull=True, admin_scope_ct__isnull=True),
            ),
        ]
        indexes = [
            postgresql_indexes.GinIndex(
                postgresql_indexes.OpClass(
                    Upper(models.Func(models.F('name'), function='public.immutable_unaccent')),
                    'public.gin_trgm_ops',
                ),
                name='name_idx',
            ),
        ]

    def natural_key(self):
        return [
            self.slug,
            self.ou and self.ou.natural_key(),
            self.service and self.service.natural_key(),
        ]

    def to_json(self):
        return {
            'uuid': self.uuid,
            'name': self.name,
            'slug': self.slug,
            'is_admin': bool(self.admin_scope_ct and self.admin_scope_id),
            'is_service': bool(self.service),
            'ou__uuid': self.ou.uuid if self.ou else None,
            'ou__name': self.ou.name if self.ou else None,
            'ou__slug': self.ou.slug if self.ou else None,
        }

    def export_json(self, parents=False, permissions=False):
        d = {
            'uuid': self.uuid,
            'slug': self.slug,
            'name': self.name,
            'description': self.description,
            'details': self.details,
            'emails': self.emails,
            'emails_to_members': self.emails_to_members,
            'is_superuser': self.is_superuser,
            'external_id': self.external_id,
            'ou': self.ou and self.ou.natural_key_json(),
            'service': self.service and self.service.natural_key_json(),
        }

        if parents:
            for parenting in RoleParenting.objects.filter(
                child_id=self.id, direct=True, deleted__isnull=True
            ):
                d.setdefault('parents', []).append(parenting.parent.natural_key_json())

        if permissions:
            for perm in self.permissions.all():
                d.setdefault('permissions', []).append(perm.export_json())

        return d

    def get_absolute_url(self):
        return reverse('a2-manager-role-members', kwargs={'pk': self.pk})


Role._meta.natural_key = [
    ['uuid'],
    ['slug', 'ou__isnull', 'service__isnull'],
    ['name', 'ou__isnull', 'service__isnull'],
    ['slug', 'ou', 'service'],
    ['name', 'ou', 'service'],
    ['slug', 'ou', 'service__isnull'],
    ['name', 'ou', 'service__isnull'],
    ['slug', 'ou__isnull'],
    ['name', 'ou__isnull'],
    ['slug', 'ou'],
    ['name', 'ou'],
    ['slug'],
    ['name'],
]


class RoleParenting(models.Model):
    parent = models.ForeignKey(
        to=Role,
        swappable=True,
        related_name='child_relation',
        on_delete=models.CASCADE,
    )
    child = models.ForeignKey(
        to=Role,
        swappable=True,
        related_name='parent_relation',
        on_delete=models.CASCADE,
    )
    direct = models.BooleanField(default=True, blank=True)
    created = models.DateTimeField(verbose_name=_('Creation date'), auto_now_add=True)
    deleted = models.DateTimeField(verbose_name=_('Deletion date'), null=True)

    objects = managers.RoleParentingManager()
    alive = QueryManager(deleted__isnull=True)

    def natural_key(self):
        return [self.parent.natural_key(), self.child.natural_key(), self.direct]

    class Meta:
        verbose_name = _('role parenting relation')
        verbose_name_plural = _('role parenting relations')
        unique_together = (('parent', 'child', 'direct'),)
        # covering indexes
        indexes = [models.Index(fields=('child', 'parent', 'direct'))]

    def __str__(self):
        return '{} {}> {}'.format(self.parent.name, '-' if self.direct else '~', self.child.name)


class Operation(models.Model):
    slug = models.CharField(max_length=32, verbose_name=_('slug'), unique=True)

    def natural_key(self):
        return [self.slug]

    def __str__(self):
        return str(self._registry.get(self.slug, self.slug))

    def export_json(self):
        return {'slug': self.slug}

    @property
    def name(self):
        return str(self)

    @classmethod
    def register(cls, name, slug):
        cls._registry[slug] = name
        return cls(slug=slug)

    _registry = {}

    objects = managers.OperationManager()


Operation._meta.natural_key = ['slug']


GenericRelation(Permission, content_type_field='target_ct', object_id_field='target_id').contribute_to_class(
    ContentType, 'admin_perms'
)


ADMIN_OP = Operation.register(name=pgettext_lazy('permission', 'Management'), slug='admin')
CHANGE_OP = Operation.register(name=pgettext_lazy('permission', 'Change'), slug='change')
DELETE_OP = Operation.register(name=pgettext_lazy('permission', 'Delete'), slug='delete')
ADD_OP = Operation.register(name=pgettext_lazy('permission', 'Add'), slug='add')
VIEW_OP = Operation.register(name=pgettext_lazy('permission', 'View'), slug='view')
SEARCH_OP = Operation.register(name=pgettext_lazy('permission', 'Search'), slug='search')
CHANGE_PASSWORD_OP = Operation.register(name=_('Change password'), slug='change_password')
RESET_PASSWORD_OP = Operation.register(name=_('Password reset'), slug='reset_password')
ACTIVATE_OP = Operation.register(name=_('Activation'), slug='activate')
CHANGE_EMAIL_OP = Operation.register(name=pgettext_lazy('operation', 'Change email'), slug='change_email')
MANAGE_MEMBERS_OP = Operation.register(name=_('Manage role members'), slug='manage_members')
MANAGE_AUTHORIZATIONS_OP = Operation.register(name=_('Manage service consents'), slug='manage_authorizations')
