# authentic2 - versatile identity manager
# Copyright (C) 2022 Entr'ouvert
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
import binascii
import datetime
import logging
import os
import uuid

from django.apps import apps
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max
from django.shortcuts import render, reverse
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from authentic2 import views
from authentic2.a2_rbac.models import Role
from authentic2.data_transfer import search_ou, search_role
from authentic2.manager.utils import label_from_role
from authentic2.models import Attribute
from authentic2.utils.evaluate import condition_validator, evaluate_condition
from authentic2.utils.template import validate_condition_template

from .query import AuthenticatorManager

logger = logging.getLogger(__name__)


class AuthenticatorImportError(Exception):
    pass


class BaseAuthenticator(models.Model):
    uuid = models.CharField(max_length=255, unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_('Name'), blank=True, max_length=128)
    slug = models.SlugField(unique=True)
    ou = models.ForeignKey(
        verbose_name=_('organizational unit'),
        to='a2_rbac.OrganizationalUnit',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    order = models.IntegerField(_('Order'), default=0, editable=False)
    enabled = models.BooleanField(default=False, editable=False)
    show_condition = models.CharField(
        _('Show condition'),
        max_length=1024,
        blank=True,
        default='',
        help_text=_(
            'Condition controlling authenticator display. For example, "is_for_backoffice()" would show the authenticator only for backoffice access, '
            '"is_for_frontoffice()" would show the authenticator only for frontoffice access. '
            'Advanced configuration can be performed, for example "is_for_backoffice() or remote_addr == \'1.2.3.4\'" '
            'would hide the authenticator from frontoffice users except if they come from the specified IP address. '
            'Available variables include service_ou_slug, service_slug, remote_addr, login_hint and headers.'
        ),
        validators=[condition_validator],
    )
    allow_user_change_email = models.BooleanField(_('Allow user change email'), default=True)
    button_description = models.CharField(
        _('Login block description'),
        max_length=256,
        blank=True,
        help_text=_('Description will be shown at the top of login block (unless already set by theme).'),
    )
    button_label = models.CharField(_('Login button label'), max_length=256, default=_('Login'))
    button_image = models.ImageField(
        _('Login button image'),
        blank=True,
        upload_to='authenticators/button_images',
        help_text=_(
            'When an image is set, login button label must contain a text to describe it, to guarantee correct website accessibility.'
        ),
    )

    objects = models.Manager()
    authenticators = AuthenticatorManager()

    type = ''
    related_models = {}
    related_object_form_class = None
    manager_view_template_name = 'authentic2/authenticators/authenticator_detail.html'
    unique = False
    protected = False
    description_fields = ['show_condition']
    empty_field_labels = {'show_condition': pgettext_lazy('show condition', 'None')}

    class Meta:
        ordering = ('-enabled', 'order', 'name', 'slug', 'ou')

    def __str__(self):
        if not self.unique:
            return '%s - %s' % (self._meta.verbose_name, self.name or self.slug)
        return str(self._meta.verbose_name)

    @property
    def manager_form_classes(self):
        return [(_('General'), self.manager_form_class)]

    def get_identifier(self):
        return self.type if self.unique else '%s_%s' % (self.type, self.slug)

    def get_absolute_url(self):
        return reverse('a2-manager-authenticator-detail', kwargs={'pk': self.pk})

    def get_short_description(self):
        return ''

    def get_full_description(self):
        for field in self.description_fields:
            if hasattr(self, 'get_%s_display' % field):
                value = getattr(self, 'get_%s_display' % field)()
            else:
                value = getattr(self, field)

            value = value or self.empty_field_labels.get(field)
            if not value:
                continue

            if isinstance(value, datetime.datetime):
                value = date_format(value, 'DATETIME_FORMAT')
            elif isinstance(value, bool):
                value = _('Yes') if value else _('No')

            yield format_html(
                _('{field}: {value}'),
                field=capfirst(self._meta.get_field(field).verbose_name),
                value=value,
            )

    def is_for_office(self, office_keyword, ctx):
        try:
            return evaluate_condition(
                settings.AUTHENTICATOR_SHOW_CONDITIONS[office_keyword], ctx, on_raise=False
            )
        except Exception as e:
            logger.error(e)
            return False

    def shown(self, ctx=()):
        if not self.show_condition:
            return True

        def is_for_backoffice():
            return self.is_for_office('is_for_backoffice', ctx)

        def is_for_frontoffice():
            return self.is_for_office('is_for_frontoffice', ctx)

        ctx = dict(
            ctx, id=self.slug, is_for_backoffice=is_for_backoffice, is_for_frontoffice=is_for_frontoffice
        )
        try:
            return evaluate_condition(self.show_condition, ctx, on_raise=True)
        except Exception as e:
            logger.error(e)
            return False

    def has_valid_configuration(self, exclude=None):
        exclude = exclude or set()
        for _, form_class in self.manager_form_classes:
            form_exclude = exclude
            form_exclude |= set(getattr(form_class._meta, 'exclude', None) or [])
            try:
                self.full_clean(exclude=form_exclude or None)
            except ValidationError:
                return False
        return True

    def export_json(self):
        data = {
            'authenticator_type': '%s.%s' % (self._meta.app_label, self._meta.model_name),
        }

        fields = [
            f
            for f in self._meta.get_fields()
            if not f.is_relation and not f.auto_created and f.editable and f.attname != 'button_image'
        ]

        data.update({field.name: getattr(self, field.attname) for field in fields})

        if self.button_image:
            b64content = base64.b64encode(self.button_image.read()).decode('ascii')
            data['button_image'] = [os.path.basename(self.button_image.name), b64content]

        data['ou'] = self.ou and self.ou.natural_key_json()
        data['related_objects'] = [obj.export_json() for qs in self.related_models.values() for obj in qs]

        return data

    @classmethod
    def import_json(cls, data):
        def get_model_from_dict(data, key):
            try:
                model_name = data.pop(key)
            except KeyError:
                raise AuthenticatorImportError(_('Missing "%s" key.') % key)

            try:
                return apps.get_model(model_name)
            except LookupError:
                raise AuthenticatorImportError(
                    _('Unknown %(key)s: %(value)s.') % {'key': key, 'value': model_name}
                )
            except ValueError:
                raise AuthenticatorImportError(
                    _('Invalid %(key)s: %(value)s.') % {'key': key, 'value': model_name}
                )

        related_objects = data.pop('related_objects', [])

        if 'button_image' in data:
            try:
                name, content = data['button_image']
            except (ValueError, TypeError):
                raise AuthenticatorImportError(
                    _('Invalid button_image: expect an array [NAME, BASE64_CONTENT]')
                )
            try:
                content = base64.standard_b64decode(content)
            except binascii.Error:
                raise AuthenticatorImportError(_('Invalid button_image: invalid base64'))
            data['button_image'] = ContentFile(content, name=name)
            try:
                cls.button_image.field.formfield().to_python(data['button_image'])
            except ValidationError:
                raise AuthenticatorImportError(_('Invalid button_image: base64 content is not a valid image'))

        ou = data.pop('ou', None)
        if ou:
            data['ou'] = search_ou(ou)
            if not data['ou']:
                raise AuthenticatorImportError(_('Organization unit not found: %s.') % ou)

        model = get_model_from_dict(data, 'authenticator_type')
        try:
            slug = data.pop('slug')
        except KeyError:
            raise AuthenticatorImportError(_('Missing slug.'))
        authenticator, created = model.objects.update_or_create(slug=slug, defaults=data)

        for obj in related_objects:
            model = get_model_from_dict(obj, 'object_type')
            model.import_json(obj, authenticator)

        if created:
            max_order = BaseAuthenticator.objects.aggregate(max=Max('order'))['max'] or 0
            authenticator.order = max_order + 1
            authenticator.save()

        return authenticator, created


def sms_code_duration_help_text():
    return _('Time (in seconds, between 60 and 3600) after which SMS codes expire. Default is {}.').format(
        settings.SMS_CODE_DURATION
    )


class AuthenticatorRelatedObjectBase(models.Model):
    authenticator = models.ForeignKey(BaseAuthenticator, on_delete=models.CASCADE)

    class Meta:
        abstract = True

    def get_journal_text(self):
        return '%s (%s)' % (self._meta.verbose_name, self.pk)

    @property
    def model_name(self):
        return self._meta.model_name

    @property
    def verbose_name_plural(self):
        return self._meta.verbose_name_plural

    def export_json(self):
        data = {
            'object_type': '%s.%s' % (self._meta.app_label, self._meta.model_name),
        }

        fields = [
            f for f in self._meta.get_fields() if not f.is_relation and not f.auto_created and f.editable
        ]
        data.update({field.name: getattr(self, field.attname) for field in fields})
        return data

    @classmethod
    def import_json(cls, data, authenticator):
        cls.objects.update_or_create(authenticator=authenticator, **data)


class AddRoleAction(AuthenticatorRelatedObjectBase):
    role = models.ForeignKey(Role, verbose_name=_('Role'), on_delete=models.CASCADE)
    mandatory = models.BooleanField(_('Mandatory (unused)'), editable=False, default=False)

    condition = models.CharField(
        _('Condition'),
        max_length=1024,
        blank=True,
        default='',
        help_text=_(
            'Django condition controlling role attribution. For example, "\'Admin\' in attributes.groups"'
            ' will attribute the role if attributes has "groups" attribute containing the value'
            ' "Admin". Variable "attributes" contains the attributes received from the identity provider. '
            'If condition is not satisfied the role will be removed.'
        ),
        validators=[validate_condition_template],
    )

    description = _('Add roles to users on successful login.')

    class Meta:
        default_related_name = 'add_role_actions'
        verbose_name = _('Add a role')
        verbose_name_plural = _('Add roles')
        ordering = ('role__ou__name', 'role__name', 'id')

    def __str__(self):
        if self.condition:
            return _('%s (depending on condition)') % (label_from_role(self.role))
        return label_from_role(self.role)

    def export_json(self):
        data = super().export_json()
        data['role'] = self.role.natural_key_json()
        return data

    @classmethod
    def import_json(cls, data, authenticator):
        try:
            role = data.pop('role')
        except KeyError:
            raise AuthenticatorImportError(_('Missing "role" key in add role action.'))

        data['role'] = search_role(role)
        if not data['role']:
            raise AuthenticatorImportError(_('Role not found: %s.') % role)

        super().import_json(data, authenticator)


class LoginPasswordAuthenticator(BaseAuthenticator):
    MIN_PASSWORD_STRENGTH_CHOICES = (
        (None, _('Follow static checks')),
        (0, _('Very Weak')),
        (1, _('Weak')),
        (2, _('Fair')),
        (3, _('Good')),
        (4, _('Strong')),
    )

    registration_open = models.BooleanField(
        _('Registration open'), default=True, help_text=_('Allow users to create accounts.')
    )
    registration_forbidden_email_domains = ArrayField(
        models.CharField(max_length=128),
        blank=True,
        default=list,
        verbose_name=_('Email domains forbidden for registration'),
        help_text=_('Comma separated list of domains (example : "@gmail.com, @outlook.fr")'),
    )
    remember_me = models.PositiveIntegerField(
        _('Remember me duration'),
        blank=True,
        null=True,
        help_text=_(
            'Session duration as seconds when using the remember me checkbox. Leave blank to hide the checkbox.'
        ),
        validators=[
            MinValueValidator(
                3600 * 8,
                _('Ensure that this value is higher than eight hours, or leave blank for default value.'),
            ),
            MaxValueValidator(
                3600 * 24 * 90,
                _('Ensure that this value is lower than three months, or leave blank for default value.'),
            ),
        ],
    )
    include_ou_selector = models.BooleanField(_('Include OU selector in login form'), default=False)
    accept_email_authentication = models.BooleanField(
        _('Let the users identify with their email address'), default=True
    )
    accept_phone_authentication = models.BooleanField(
        _('Let the users identify with their phone number'), default=False
    )
    phone_identifier_field = models.ForeignKey(
        Attribute,
        verbose_name=_('Phone field used as user identifier'),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    min_password_strength = models.IntegerField(
        verbose_name=_('Minimum password strength'),
        choices=MIN_PASSWORD_STRENGTH_CHOICES,
        default=3,
        blank=True,
        null=True,
        help_text=_(
            'Password strength, using dynamic indicators such as common names, dates and other '
            'popular patterns. Selecting "static checks" will instead validate that a password '
            'contains enough different kind of caracters. Password indicator on registration '
            'form will reflect the chosen policy.'
        ),
    )
    password_min_length = models.PositiveIntegerField(_('Password minimum length'), default=8, null=True)
    password_regex = models.CharField(
        _('Regular expression for validating passwords'), max_length=512, blank=True, default=''
    )
    password_regex_error_msg = models.CharField(
        _('Error message to show when the password do not validate the regular expression'),
        max_length=1024,
        blank=True,
        default='',
    )

    login_exponential_retry_timeout_duration = models.FloatField(
        _('Retry timeout duration'),
        default=1,
        help_text=_(
            'Exponential backoff base factor duration as seconds until next try after a login failure.'
        ),
    )
    login_exponential_retry_timeout_factor = models.FloatField(
        _('Retry timeout factor'),
        default=1.8,
        help_text=_('Exponential backoff factor duration as seconds until next try after a login failure.'),
    )
    login_exponential_retry_timeout_max_duration = models.PositiveIntegerField(
        _('Retry timeout max duration'),
        default=3600,
        help_text=_(
            'Maximum exponential backoff maximum duration as seconds until next try after a login failure.'
        ),
    )
    login_exponential_retry_timeout_min_duration = models.PositiveIntegerField(
        _('Backoff activation threshold'),
        default=10,
        help_text=_('Minimum duration in seconds above which the computed backoff starts to apply.'),
    )

    emails_ip_ratelimit = models.CharField(
        _('Emails IP ratelimit'),
        default='10/h',
        max_length=32,
        help_text=_('Maximum rate of email sendings triggered by the same IP address.'),
    )
    sms_ip_ratelimit = models.CharField(
        _('SMS IP ratelimit'),
        default='10/h',
        max_length=32,
        help_text=_('Maximum rate of SMSs triggered by the same IP address.'),
    )
    emails_address_ratelimit = models.CharField(
        _('Emails address ratelimit'),
        default='3/d',
        max_length=32,
        help_text=_('Maximum rate of emails sent to the same email address.'),
    )
    sms_number_ratelimit = models.CharField(
        _('SMS number ratelimit'),
        default='10/h',
        max_length=32,
        help_text=_('Maximum rate of SMSs sent to the same phone number.'),
    )
    sms_code_duration = models.PositiveSmallIntegerField(
        _('SMS codes lifetime (in seconds)'),
        help_text=sms_code_duration_help_text,
        validators=[
            MinValueValidator(
                60, _('Ensure that this value is higher than 60, or leave blank for default value.')
            ),
            MaxValueValidator(
                3600, _('Ensure that this value is lower than 3600, or leave blank for default value.')
            ),
        ],
        null=True,
        blank=True,
    )

    type = 'password'
    how = ['password', 'password-on-https']
    unique = True
    protected = True

    class Meta:
        verbose_name = _('Password')

    @property
    def is_phone_authn_active(self):
        return bool(self.accept_phone_authentication and self.phone_identifier_field)

    @property
    def manager_form_classes(self):
        from .forms import LoginPasswordAuthenticatorAdvancedForm, LoginPasswordAuthenticatorEditForm

        return [
            (_('General'), LoginPasswordAuthenticatorEditForm),
            (_('Advanced'), LoginPasswordAuthenticatorAdvancedForm),
        ]

    def login(self, request, *args, **kwargs):
        return views.login_password_login(request, self, *args, **kwargs)

    def profile(self, request, *args, **kwargs):
        return views.login_password_profile(request, *args, **kwargs)

    def registration(self, request, *args, **kwargs):
        context = kwargs.get('context', {})
        context['is_phone_authn_active'] = self.is_phone_authn_active
        return render(request, 'authentic2/login_password_registration_form.html', context)

    def is_origin_for_user(self, user):
        return not user.is_external_account()
