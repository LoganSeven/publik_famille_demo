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


import base64
import datetime
import functools
import operator
import os
import uuid

from django.contrib import auth
from django.contrib.auth.models import AbstractBaseUser, Group
from django.contrib.auth.models import Permission as AuthPermission
from django.contrib.auth.models import _user_has_module_perms, _user_has_perm
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import MultipleObjectsReturned, ValidationError
from django.core.mail import send_mail
from django.db import models, transaction
from django.db.models import indexes
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

try:
    from django.contrib.auth.models import _user_get_all_permissions
except ImportError:
    from django.contrib.auth.models import _user_get_permissions

    def _user_get_all_permissions(user, obj):
        return _user_get_permissions(user, obj, 'all')


from django.db.models import JSONField

from authentic2 import app_settings
from authentic2.a2_rbac.models import RoleParenting
from authentic2.decorators import errorcollector
from authentic2.models import Attribute, AttributeValue, Service, Setting
from authentic2.utils import misc as utils_misc
from authentic2.utils.cache import RequestCache
from authentic2.utils.misc import get_password_authenticator
from authentic2.utils.models import generate_slug
from authentic2.validators import PhoneNumberValidator, email_validator

from .backends import DjangoRBACBackend
from .managers import UserManager, UserQuerySet


@RequestCache
def get_attributes_map():
    mapping = {}
    for at in Attribute.objects.all():
        mapping[at.id] = at
        mapping[at.name] = at
    return mapping


def iter_attributes():
    for key, value in get_attributes_map().items():
        if isinstance(key, str):
            yield value


class Attributes:
    def __init__(self, owner, verified=None):
        self.__dict__['owner'] = owner
        self.__dict__['verified'] = verified
        if not hasattr(self.owner, '_a2_attributes_cache'):
            values = {}
            setattr(self.owner, '_a2_attributes_cache', values)
            for atv in self.owner.attribute_values.filter(attribute__disabled=False):
                attribute = get_attributes_map()[atv.attribute_id]
                atv.attribute = attribute
                if attribute.multiple:
                    values.setdefault(attribute.name, []).append(atv)
                else:
                    values[attribute.name] = atv
        self.__dict__['values'] = owner._a2_attributes_cache

    def __setattr__(self, name, value):
        attribute = get_attributes_map().get(name)
        if not attribute:
            raise AttributeError(name)

        with transaction.atomic():
            if attribute.multiple:
                attribute.set_value(
                    self.owner,
                    value,
                    verified=bool(self.verified),
                )
            else:
                atv = self.values.get(name)
                self.values[name] = attribute.set_value(
                    self.owner,
                    value,
                    verified=bool(self.verified),
                    attribute_value=atv,
                )

            update_fields = ['modified']
            if name in ['first_name', 'last_name']:
                if getattr(self.owner, name) != value:
                    setattr(self.owner, name, value)
                    update_fields.append(name)
            self.owner.save(update_fields=update_fields)

    def __getattr__(self, name):
        if name not in get_attributes_map():
            raise AttributeError(name)
        atv = self.values.get(name)
        if self.verified and (not atv or not atv.verified):
            return None
        if atv:
            if not isinstance(atv, (list, tuple)):
                return atv.to_python()
            else:
                # multiple
                return [x.to_python() for x in atv]
        return None


class AttributesDescriptor:
    def __init__(self, verified=None):
        self.verified = verified

    def __get__(self, obj, objtype):
        return Attributes(obj, verified=self.verified)


class IsVerified:
    def __init__(self, user):
        self.user = user

    def __getattr__(self, name):
        v = getattr(self.user.attributes, name, None)
        return v is not None and v == getattr(self.user.verified_attributes, name, None)


class IsVerifiedDescriptor:
    def __get__(self, obj, objtype):
        return IsVerified(obj)


class User(AbstractBaseUser):
    """
    An abstract base class implementing a fully featured User model with
    admin-compliant permissions.

    Username, password and email are required. Other fields are optional.
    """

    uuid = models.CharField(
        _('uuid'), max_length=32, default=utils_misc.get_hex_uuid, editable=False, unique=True
    )
    username = models.CharField(_('username'), max_length=256, null=True, blank=True, db_index=True)
    first_name = models.CharField(_('first name'), max_length=128, blank=True)
    last_name = models.CharField(_('last name'), max_length=128, blank=True)
    email = models.EmailField(_('email address'), blank=True, max_length=254, validators=[email_validator])
    email_verified = models.BooleanField(default=False, verbose_name=_('email verified'))
    email_verified_date = models.DateTimeField(
        default=None, blank=True, null=True, verbose_name=_('email verified date')
    )
    email_verified_sources = ArrayField(
        verbose_name=_('email verification sources'),
        base_field=models.CharField(max_length=63),
        default=list,
        null=True,
        blank=True,
    )
    is_superuser = models.BooleanField(
        _('superuser status'),
        default=False,
        help_text=_('Designates that this user has all permissions without explicitly assigning them.'),
    )
    phone = models.CharField(
        _('phone number'), null=True, blank=True, max_length=64, validators=[PhoneNumberValidator]
    )
    phone_verified_on = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        verbose_name=_('phone verification date'),
    )
    is_staff = models.BooleanField(
        _('staff status'),
        default=False,
        help_text=_('Designates whether the user can log into this admin site.'),
    )
    is_active = models.BooleanField(
        _('active'),
        default=True,
        help_text=_(
            'Designates whether this user should be treated as active. Unselect this instead of deleting'
            ' accounts.'
        ),
    )
    ou = models.ForeignKey(
        verbose_name=_('organizational unit'),
        to='a2_rbac.OrganizationalUnit',
        blank=True,
        null=True,
        swappable=False,
        on_delete=models.CASCADE,
    )
    groups = models.ManyToManyField(
        to=Group,
        verbose_name=_('groups'),
        blank=True,
        help_text=_(
            'The groups this user belongs to. A user will get all permissions granted to each of his/her'
            ' group.'
        ),
        related_name='user_set',
        related_query_name='user',
    )
    user_permissions = models.ManyToManyField(
        to=AuthPermission,
        verbose_name=_('user permissions'),
        blank=True,
        help_text=_('Specific permissions for this user.'),
        related_name='user_set',
        related_query_name='user',
    )

    # events dates
    date_joined = models.DateTimeField(_('date joined'), default=timezone.now)
    modified = models.DateTimeField(verbose_name=_('Last modification time'), db_index=True, auto_now=True)
    last_account_deletion_alert = models.DateTimeField(
        verbose_name=_('Last account deletion alert'), null=True, blank=True
    )
    deactivation = models.DateTimeField(verbose_name=_('Deactivation datetime'), null=True, blank=True)
    deactivation_reason = models.TextField(verbose_name=_('Deactivation reason'), null=True, blank=True)

    objects = UserManager.from_queryset(UserQuerySet)()
    attributes = AttributesDescriptor()
    verified_attributes = AttributesDescriptor(verified=True)
    is_verified = IsVerifiedDescriptor()

    keepalive = models.DateTimeField(verbose_name=_('Keepalive timestamp'), null=True, blank=True)

    attribute_values = GenericRelation('authentic2.AttributeValue')

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']
    USER_PROFILE = ('first_name', 'last_name', 'email')

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ('last_name', 'first_name', 'email', 'username')
        indexes = [indexes.Index('last_name', 'first_name', name='custom_user_user_names')]

    def get_group_permissions(self, obj=None):
        """
        Returns a list of permission strings that this user has through their
        groups. This method queries all available auth backends. If an object
        is passed in, only permissions matching this object are returned.
        """
        permissions = set()
        for backend in auth.get_backends():
            if hasattr(backend, 'get_group_permissions'):
                permissions.update(backend.get_group_permissions(self, obj))
        return permissions

    def get_all_permissions(self, obj=None):
        return _user_get_all_permissions(self, obj)

    def has_perm(self, perm, obj=None):
        """
        Returns True if the user has the specified permission. This method
        queries all available auth backends, but returns immediately if any
        backend returns True. Thus, a user who has permission from a single
        auth backend is assumed to have permission in general. If an object is
        provided, permissions for this specific object are checked.
        """

        # Active superusers have all permissions.
        if self.is_active and self.is_superuser:
            return True

        # Otherwise we need to check the backends.
        return _user_has_perm(self, perm, obj)

    def has_perms(self, perm_list, obj=None):
        """
        Returns True if the user has each of the specified permissions. If
        object is passed, it checks if the user has all required perms for this
        object.
        """
        # Active superusers have all permissions.
        if self.is_active and self.is_superuser:
            return True

        for perm in perm_list:
            if not self.has_perm(perm, obj):
                return False
        return True

    def has_module_perms(self, app_label):
        """
        Returns True if the user has any permissions in the given app label.
        Uses pretty much the same logic as has_perm, above.
        """
        # Active superusers have all permissions.
        if self.is_active and self.is_superuser:
            return True

        return _user_has_module_perms(self, app_label)

    def filter_by_perm(self, perm_or_perms, qs):
        results = []
        for backend in auth.get_backends():
            if hasattr(backend, 'filter_by_perm'):
                results.append(backend.filter_by_perm(self, perm_or_perms, qs))
        if results:
            return functools.reduce(operator.__or__, results)
        else:
            return qs

    def has_perm_any(self, perm_or_perms):
        # Active superusers have all permissions.
        if self.is_active and self.is_superuser:
            return True

        for backend in auth.get_backends():
            if hasattr(backend, 'has_perm_any'):
                if backend.has_perm_any(self, perm_or_perms):
                    return True
        return False

    def has_ou_perm(self, perm, ou):
        # Active superusers have all permissions.
        if self.is_active and self.is_superuser:
            return True

        for backend in auth.get_backends():
            if hasattr(backend, 'has_ou_perm'):
                if backend.has_ou_perm(self, perm, ou):
                    return True
        return False

    def ous_with_perm(self, perm, queryset=None):
        return DjangoRBACBackend().ous_with_perm(self, perm, queryset=queryset)

    def get_full_name(self):
        """
        Returns the first_name plus the last_name, with a space in between.
        """
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip() or self.username or self.email or self.phone_identifier or ''

    def get_short_name(self):
        'Returns the short name for the user.'
        return self.first_name or self.username or self.email or self.uuid[:6]

    def email_user(self, subject, message, from_email=None):
        """
        Sends an email to this User.
        """
        send_mail(subject, message, from_email, [self.email])

    def get_username(self):
        'Return the identifying username for this User'
        return self.username or self.email or self.get_full_name() or self.uuid

    def roles_and_parents(self):
        qs1 = self.roles.all()
        qs2 = qs1.model.objects.filter(
            child_relation__deleted__isnull=True,
            child_relation__child__in=qs1,
        )
        qs = (qs1 | qs2).order_by('name').distinct()
        rp_qs = RoleParenting.objects.filter(child__in=qs1)
        qs = qs.prefetch_related(models.Prefetch('child_relation', queryset=rp_qs), 'child_relation__parent')
        qs = qs.prefetch_related(
            models.Prefetch('members', queryset=self.__class__.objects.filter(pk=self.pk), to_attr='member')
        )
        return qs

    def can_change_email(self):
        confkey = 'users:can_change_email_address'
        qs = Setting.objects.filter(key=confkey)
        if qs.exists():
            can_change_setting = qs.get().value
        else:
            can_change_setting = utils_misc.RUNTIME_SETTINGS[confkey]
        if can_change_setting:
            for authenticator in utils_misc.get_authenticators():
                if authenticator.is_origin_for_user(self):
                    return authenticator.allow_user_change_email
        return can_change_setting

    def is_external_account(self):
        if (
            self.userexternalid_set.exists()
            or self.saml_identifiers.exists()
            or getattr(self, 'oidc_account', None)
        ):
            return True
        return False

    def __str__(self):
        return self.get_full_name()

    def __repr__(self):
        human_name = self.username or self.email or self.get_full_name()
        short_id = self.uuid[:6]
        return '<User: %s (%s)>' % (human_name, short_id)

    def clean(self):
        if not (self.username or self.email or self.phone or (self.first_name and self.last_name)):
            raise ValidationError(
                _(
                    'An account needs at least one identifier: username, email, phone numbor or a full name '
                    '(first and last name).'
                )
            )

    def validate_unique(self, exclude=None):
        errors = {}

        with errorcollector(errors):
            super().validate_unique(exclude=exclude)

        exclude = exclude or []

        model = self.__class__
        qs = model.objects
        if self.pk:
            qs = qs.exclude(pk=self.pk)

        if (
            'username' not in exclude
            and self.username
            and (app_settings.A2_USERNAME_IS_UNIQUE or (self.ou and self.ou.username_is_unique))
        ):
            username_qs = qs
            if not app_settings.A2_USERNAME_IS_UNIQUE:
                username_qs = qs.filter(ou=self.ou)
            try:
                try:
                    username_qs.get(username=self.username)
                except MultipleObjectsReturned:
                    pass
            except model.DoesNotExist:
                pass
            else:
                errors.setdefault('username', []).append(
                    _('This username is already in use. Please supply a different username.')
                )

        if (
            'email' not in exclude
            and self.email
            and (app_settings.A2_EMAIL_IS_UNIQUE or (self.ou and self.ou.email_is_unique))
        ):
            email_qs = qs
            if not app_settings.A2_EMAIL_IS_UNIQUE:
                email_qs = qs.filter(ou=self.ou)
            try:
                try:
                    email_qs.get(email__iexact=self.email)
                except MultipleObjectsReturned:
                    pass
            except model.DoesNotExist:
                pass
            else:
                errors.setdefault('email', []).append(
                    _('This email address is already in use. Please supply a different email address.')
                )
        if errors:
            raise ValidationError(errors)

    def natural_key(self):
        return (self.uuid,)

    def has_verified_attributes(self):
        return AttributeValue.objects.with_owner(self).filter(verified=True).exists()

    def to_json(self):
        d = {}
        attributes_map = get_attributes_map()
        for av in AttributeValue.objects.with_owner(self):
            attribute = attributes_map[av.attribute_id]
            drf_field = attribute.get_drf_field()
            d[str(attribute.name)] = drf_field.to_representation(av.to_python())
        d.update(
            {
                'uuid': self.uuid,
                'username': self.username,
                'email': self.email,
                'ou': self.ou.name if self.ou else None,
                'ou__uuid': self.ou.uuid if self.ou else None,
                'ou__slug': self.ou.slug if self.ou else None,
                'ou__name': self.ou.name if self.ou else None,
                'first_name': self.first_name,
                'last_name': self.last_name,
                'is_superuser': self.is_superuser,
                'roles': [role.to_json() for role in self.roles_and_parents()],
                'services': [
                    service.to_json(roles=self.roles_and_parents()) for service in Service.objects.all()
                ],
            }
        )
        return d

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        super().save(*args, **kwargs)
        if not update_fields or not set(update_fields).isdisjoint({'first_name', 'last_name'}):
            try:
                self.attributes.first_name
            except AttributeError:
                pass
            else:
                if self.attributes and self.attributes.first_name != self.first_name:
                    self.attributes.first_name = self.first_name
            try:
                self.attributes.last_name
            except AttributeError:
                pass
            else:
                if self.attributes.last_name != self.last_name:
                    self.attributes.last_name = self.last_name

    def can_change_password(self):
        return True

    def refresh_from_db(self, *args, **kwargs):
        if hasattr(self, '_a2_attributes_cache'):
            del self._a2_attributes_cache
        return super().refresh_from_db(*args, **kwargs)

    def mark_as_active(self):
        self.is_active = True
        self.deactivation = None
        self.deactivation_reason = None
        self.save(update_fields=['is_active', 'deactivation', 'deactivation_reason'])

    def mark_as_inactive(self, timestamp=None, reason=None):
        self.is_active = False
        self.deactivation = timestamp or timezone.now()
        self.deactivation_reason = reason
        self.save(update_fields=['is_active', 'deactivation', 'deactivation_reason'])

    @property
    def phone_identifier(self):
        if field := get_password_authenticator().phone_identifier_field:
            atvs = (
                AttributeValue.objects.with_owner(self)
                .filter(
                    attribute=field,
                    content__isnull=False,
                )
                .values_list('content', flat=True)
            )
            if atvs:
                return atvs[0]
        return None

    @property
    def verbose_deactivation_reason(self):
        from authentic2.backends.ldap_backend import (
            LDAP_DEACTIVATION_REASON_NOT_PRESENT,
            LDAP_DEACTIVATION_REASON_OLD_SOURCE,
        )

        if self.deactivation_reason == LDAP_DEACTIVATION_REASON_NOT_PRESENT:
            return _('associated LDAP account does not exist anymore')
        elif self.deactivation_reason == LDAP_DEACTIVATION_REASON_OLD_SOURCE:
            return _('associated LDAP source has been deleted')
        else:
            return self.deactivation_reason

    def set_random_password(self):
        self.set_password(base64.b64encode(os.urandom(32)).decode('ascii'))

    @transaction.atomic
    def delete(self, **kwargs):
        deleted_user = DeletedUser(old_user_id=self.id)
        if 'email' in app_settings.A2_USER_DELETED_KEEP_DATA:
            deleted_user.old_email = self.email.rsplit('#', 1)[0]
        if 'uuid' in app_settings.A2_USER_DELETED_KEEP_DATA:
            deleted_user.old_uuid = self.uuid
        if 'phone' in app_settings.A2_USER_DELETED_KEEP_DATA:
            deleted_user.old_phone = self.phone

        # save LDAP account references
        external_ids = self.userexternalid_set.order_by('id')
        if external_ids.exists():
            deleted_user.old_data = {'external_ids': []}
            for external_id in external_ids:
                deleted_user.old_data['external_ids'].append(
                    {
                        'source': external_id.source,
                        'external_id': external_id.external_id,
                    }
                )
            external_ids.delete()
        deleted_user.save()
        return super().delete(**kwargs)

    def get_missing_required_on_login_attributes(self):
        attributes = Attribute.objects.filter(required_on_login=True, disabled=False).order_by(
            'order', 'label'
        )

        missing = []
        for attribute in attributes:
            value = getattr(self.attributes, attribute.name, None)
            if not value:
                missing.append(attribute)
        return missing

    def get_absolute_url(self):
        return reverse('a2-manager-user-detail', kwargs={'pk': self.pk})

    def set_email_verified(self, value, source=None):
        if bool(value):
            if isinstance(value, datetime.datetime):
                self.email_verified = True
                self.email_verified_date = value
            else:
                self.email_verified = True
                self.email_verified_date = timezone.now()
            if source and source not in self.email_verified_sources:
                self.email_verified_sources.append(source)
        else:
            if source and source in self.email_verified_sources:
                self.email_verified_sources.remove(source)
            if not source or not self.email_verified_sources:
                self.email_verified = False
                self.email_verified_date = None

    def add_role(self, role, ou=None):
        from authentic2.a2_rbac.models import Role

        if isinstance(role, Role):
            role.members.add(self)
        elif isinstance(role, str):
            Role.objects.get(name=role).members.add(self)


class DeletedUser(models.Model):
    deleted = models.DateTimeField(verbose_name=_('Deletion date'), auto_now_add=True)
    old_uuid = models.TextField(verbose_name=_('Old UUID'), null=True, blank=True, db_index=True)
    old_user_id = models.PositiveIntegerField(verbose_name=_('Old user id'), null=True, blank=True)
    old_email = models.EmailField(verbose_name=_('Old email adress'), null=True, blank=True, db_index=True)
    old_phone = models.CharField(verbose_name=_('Old phone number'), null=True, blank=True, max_length=64)
    old_data = JSONField(verbose_name=_('Old data'), null=True, blank=True)

    @classmethod
    def cleanup(cls, threshold=None, timestamp=None):
        threshold = threshold or (
            timezone.now() - datetime.timedelta(days=app_settings.A2_USER_DELETED_KEEP_DATA_DAYS)
        )
        cls.objects.filter(deleted__lt=threshold).delete()

    def __repr__(self):
        return 'DeletedUser(old_id=%s, old_uuid=%s…, old_email=%s, old_phone=%s)' % (
            self.old_user_id or '-',
            (self.old_uuid or '')[:6],
            self.old_email or '-',
            self.old_phone or '-',
        )

    def __str__(self):
        data = ['#%d' % self.old_user_id]
        if self.old_email:
            data.append(self.old_email)
        if self.old_phone:
            data.append(self.old_phone)
        return _('deleted user (%s)') % ', '.join(data)

    class Meta:
        verbose_name = _('deleted user')
        verbose_name_plural = _('deleted users')
        ordering = ('deleted', 'id')


class ProfileType(models.Model):
    uuid = models.UUIDField(verbose_name=_('UUID'), unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, verbose_name=_('name'))
    slug = models.SlugField(verbose_name=_('slug'), max_length=64, unique=True)

    services = models.ManyToManyField(
        to=Service,
        verbose_name=_('allowed services for this profile type'),
        through='ServiceProfileType',
        blank=True,
        related_name='profile_types+',
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            cls = type(self)
            seen_slugs = cls.objects.values_list('slug', flat=True)
            self.slug = generate_slug(self.name, seen_slugs=seen_slugs, max_length=64)
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = _('profile type')
        verbose_name_plural = _('profile types')
        ordering = ('name', 'slug')


class Profile(models.Model):
    profile_type = models.ForeignKey(
        verbose_name=_('profile type'),
        to=ProfileType,
        related_name='profiles',
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        to=User, verbose_name=_('user'), related_name='profiles', on_delete=models.CASCADE
    )
    identifier = models.CharField(max_length=256, verbose_name=_('identifier'), default='')
    email = models.EmailField(blank=True, max_length=254, verbose_name=_('email address'))
    data = JSONField(verbose_name=_('data'), null=True, blank=True)

    class Meta:
        unique_together = (('user', 'profile_type', 'identifier'),)
        verbose_name = _('profile')
        verbose_name_plural = _('profiles')
        ordering = ('user', 'profile_type')


class ServiceProfileType(models.Model):
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    profile_type = models.ForeignKey(ProfileType, on_delete=models.CASCADE)

    class Meta:
        unique_together = (('service', 'profile_type'),)
