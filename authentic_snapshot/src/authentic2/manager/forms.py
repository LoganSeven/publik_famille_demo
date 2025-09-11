# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import csv
import json
import logging
import smtplib
from collections import defaultdict
from io import StringIO

import netaddr
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db.models import OuterRef, Q, Subquery
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext
from django_select2.forms import HeavySelect2Widget

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import generate_slug, get_default_ou
from authentic2.custom_user.backends import DjangoRBACBackend
from authentic2.forms.fields import (
    CheckPasswordField,
    CommaSeparatedCharField,
    NewPasswordField,
    ValidatedEmailField,
)
from authentic2.forms.mixins import SlugMixin
from authentic2.forms.profile import BaseUserForm
from authentic2.models import (
    APIClient,
    Attribute,
    AttributeValue,
    PasswordReset,
    Service,
    Setting,
    UserImport,
)
from authentic2.passwords import (
    generate_apiclient_password,
    generate_password,
    validate_apiclient_password,
    validate_password,
)
from authentic2.utils.misc import (
    RUNTIME_SETTINGS,
    get_password_authenticator,
    send_email_change_email,
    send_password_reset_mail,
    send_templated_mail,
)
from authentic2.validators import EmailValidator

from . import app_settings, fields, user_import, utils

User = get_user_model()
ChooseRolesField = fields.ChooseRolesField
logger = logging.getLogger(__name__)


class CssClass:
    pass


class FormWithRequest(forms.Form):
    need_request = True

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super().__init__(*args, **kwargs)


class PrefixFormMixin:
    def __init__(self, *args, **kwargs):
        kwargs['prefix'] = self.__class__.prefix
        super().__init__(*args, **kwargs)


class LimitQuerysetFormMixin(FormWithRequest):
    """Limit queryset of all model choice field based on the objects
    viewable by the user.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.request and not self.request.user.is_anonymous:
            for field in self.fields.values():
                qs = getattr(field, 'queryset', None)
                if not qs:
                    continue
                perm = getattr(field.widget, 'perm', 'search')
                app_label = qs.model._meta.app_label
                model_name = qs.model._meta.model_name
                perm = '%s.%s_%s' % (app_label, perm, model_name)
                field.queryset = self.request.user.filter_by_perm(perm, qs)


class ChooseUserForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    user = fields.ChooseUserField(label=_('Add an user'))
    action = forms.CharField(initial='add', widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        ou = kwargs.pop('ou', None)
        super().__init__(*args, **kwargs)
        # Filter user by ou if asked
        if ou:
            self.fields['user'].queryset = self.fields['user'].queryset.filter(ou=ou)


class ChooseRoleForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    role = fields.ChooseRoleField(label=_('Add a role'))
    action = forms.CharField(initial='add', widget=forms.HiddenInput)


class UsersForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    users = fields.ChooseUsersField(label=_('Add some users'))


class RolesForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    roles = fields.ChooseRolesField(label=_('Add some roles'))


class RoleParentForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    role = fields.ChooseManageableMemberRoleField(label=_('Add some roles'))
    action = forms.CharField(initial='add', widget=forms.HiddenInput)


class ChooseUserRoleForm(LimitQuerysetFormMixin, CssClass, forms.Form):
    role = fields.ChooseManageableMemberRoleField(label=_('Add a role'))
    action = forms.CharField(initial='add', widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        ou = kwargs.pop('ou', None)
        super().__init__(*args, **kwargs)
        # Filter roles by ou if asked
        if ou:
            self.fields['role'].queryset = self.fields['role'].queryset.filter(ou=ou)


class ChooseUserAuthorizationsForm(CssClass, forms.Form):
    authorization = fields.ChooseUserAuthorizationsField()


class UserEditForm(LimitQuerysetFormMixin, CssClass, BaseUserForm):
    css_class = 'user-form'
    form_id = 'id_user_edit_form'

    def __init__(self, *args, **kwargs):
        request = kwargs.get('request')

        super().__init__(*args, **kwargs)
        if 'ou' in self.fields and not request.user.is_superuser:
            field = self.fields['ou']
            field.required = True
            qs = field.queryset
            if self.instance and self.instance.pk:
                perm = 'custom_user.change_user'
            else:
                perm = 'custom_user.add_user'
            qs = DjangoRBACBackend().ous_with_perm(request.user, perm)
            field.queryset = qs
            count = qs.count()
            if count == 1:
                field.initial = qs[0].pk
            if count < 2:
                field.widget.attrs['disabled'] = ''
            if self.is_bound and count == 1:
                self.data._mutable = True
                self.data[self.add_prefix('ou')] = qs[0].pk
                self.data._mutable = False

    def clean(self):
        super().clean()
        authn = get_password_authenticator()
        if (
            authn.is_phone_authn_active
            and (name := authn.phone_identifier_field.name)
            and self.cleaned_data.get(name, '')
        ):
            phone = self.cleaned_data.get('phone')
            if phone and a2_app_settings.A2_PHONE_IS_UNIQUE:
                if (
                    AttributeValue.objects.filter(
                        content_type=ContentType.objects.get_for_model(get_user_model()),
                        attribute=authn.phone_identifier_field,
                        content=phone,
                    )
                    .exclude(object_id=self.instance.id)
                    .exists()
                ):
                    raise ValidationError(_('This phone number identifier is already used.'))
            elif phone and self.instance.ou and self.instance.ou.phone_is_unique:
                same_phone = AttributeValue.objects.filter(
                    content_type=ContentType.objects.get_for_model(get_user_model()),
                    attribute=authn.phone_identifier_field,
                    content=phone,
                    object_id=OuterRef('pk'),
                )
                if User.objects.filter(
                    pk__in=Subquery(same_phone.values_list('object_id', flat=True)),
                    ou=self.instance.ou,
                ).exclude(pk=self.instance.pk):
                    raise ValidationError(
                        _(
                            'This phone number identifier is already used within organizational unit {ou}.'
                        ).format(ou=self.instance.ou)
                    )

    class Meta:
        model = User
        exclude = (
            'is_staff',
            'groups',
            'user_permissions',
            'last_login',
            'date_joined',
            'password',
            'keepalive',
        )


class UserChangePasswordForm(CssClass, forms.ModelForm):
    error_messages = {
        'password_mismatch': _("The two password fields didn't match."),
    }
    notification_template_prefix = 'authentic2/manager/change-password-notification'
    require_password = True

    def clean_password1(self):
        password = self.cleaned_data.get('password1')
        validate_password(password, user=self.instance)
        return password

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(
                self.error_messages['password_mismatch'],
                code='password_mismatch',
            )
        return password2

    def clean(self):
        super().clean()
        if (
            self.require_password
            and not self.cleaned_data.get('generate_password')
            and not self.cleaned_data.get('password1')
            and not self.cleaned_data.get('send_password_reset')
        ):
            raise forms.ValidationError(
                _('You must choose password generation or type a new one or send a password reset mail')
            )
        if not self.has_email() and (
            self.cleaned_data.get('send_mail')
            or self.cleaned_data.get('generate_password')
            or self.cleaned_data.get('send_password_reset')
        ):
            raise forms.ValidationError(
                _('User does not have a mail, we cannot send the informations to him.')
            )

    def has_email(self):
        return bool(self.instance and self.instance.email)

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = None
        if self.cleaned_data.get('generate_password'):
            new_password = generate_password()
            self.cleaned_data['send_mail'] = True
        elif self.cleaned_data.get('password1'):
            new_password = self.cleaned_data['password1']

        if new_password:
            user.set_password(new_password)

        if commit:
            user.save()
            if hasattr(self, 'save_m2m'):
                self.save_m2m()

        if not self.cleaned_data.get('send_password_reset'):
            if self.cleaned_data['send_mail']:
                send_templated_mail(
                    user,
                    self.notification_template_prefix,
                    context={'new_password': new_password, 'user': user},
                )
        return user

    generate_password = forms.BooleanField(initial=False, label=_('Generate new password'), required=False)
    password1 = NewPasswordField(label=_('Password'), required=False)
    password2 = CheckPasswordField(label=_('Confirmation'), required=False)
    send_mail = forms.BooleanField(initial=False, label=_('Send informations to user'), required=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fields['password1'].min_strength = get_password_authenticator().min_password_strength

    class Meta:
        model = User
        fields = ()


class UserAddForm(UserChangePasswordForm, UserEditForm):
    css_class = 'user-form'
    form_id = 'id_user_add_form'
    require_password = False

    notification_template_prefix = 'authentic2/manager/new-account-notification'
    reset_password_at_next_login = forms.BooleanField(
        initial=False, label=_('Ask for password reset on next login'), required=False
    )

    send_password_reset = forms.BooleanField(
        initial=False, label=_('Send mail to user to make it choose a password'), required=False
    )

    def __init__(self, *args, **kwargs):
        self.ou = kwargs.pop('ou', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        self.instance.ou = self.ou
        super().clean()
        # check if this account is going to be real online account, i.e. with a
        # password, it it's the case complain that there is no identifiers.
        has_password = (
            self.cleaned_data.get('new_password1')
            or self.cleaned_data.get('generate_password')
            or self.cleaned_data.get('send_password_reset')
        )

        if has_password and not self.cleaned_data.get('username') and not self.cleaned_data.get('email'):
            raise forms.ValidationError(
                _('You must set a username or an email to set a password or send an activation link.')
            )

        if not has_password:
            self.instance.set_random_password()

    def has_email(self):
        return bool(self.cleaned_data.get('email'))

    def save(self, commit=True):
        user = super().save(commit=commit)
        if self.cleaned_data.get('reset_password_at_next_login'):
            if commit:
                PasswordReset.objects.get_or_create(user=user)
            else:
                old_save = user.save

                def save(*args, **kwargs):
                    old_save(*args, **kwargs)
                    PasswordReset.objects.get_or_create(user=user)

                user.save = save
        if self.cleaned_data.get('send_password_reset'):
            try:
                send_password_reset_mail(
                    user,
                    template_names=[
                        'authentic2/manager/user_create_registration_email',
                        'authentic2/password_reset',
                    ],
                    request=self.request,
                    next_url='/accounts/',
                    context={
                        'user': user,
                    },
                )
            except smtplib.SMTPException as e:
                logger.error(
                    'registration mail could not be sent to user %s created through manager: %s', user, e
                )
        return user

    class Meta:
        model = User
        fields = '__all__'
        exclude = ('ou',)


class ServiceRoleSearchForm(CssClass, PrefixFormMixin, FormWithRequest):
    prefix = 'search'

    text = forms.CharField(label=_('Name'), required=False)
    internals = forms.BooleanField(initial=False, label=_('Show internal roles'), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if app_settings.SHOW_INTERNAL_ROLES:
            del self.fields['internals']

    def filter(self, qs):
        if hasattr(super(), 'filter'):
            qs = super().filter(qs)
        text = (self.cleaned_data.get('text') or '').strip()
        if not app_settings.SHOW_INTERNAL_ROLES and not self.cleaned_data.get('internals'):
            qs = qs.exclude(slug__startswith='_')
        if text:
            condition = Q()
            for word in (w.strip() for w in text.split(' ')):
                if not word:
                    continue
                condition &= Q(name__immutable_unaccent__icontains=word)
            qs = qs.filter(condition | Q(slug__startswith=text))
        return qs


class HideOUFieldMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if utils.get_ou_count() < 2:
            del self.fields['ou']

    def clean(self):
        if 'ou' not in self.fields:
            self.instance.ou = get_default_ou()
        return super().clean()


class OUSearchForm(FormWithRequest):
    ou_permission = None
    queryset = None

    ou = forms.ChoiceField(label=_('Organizational unit'), required=False)

    def __init__(self, *args, **kwargs):
        # if there are many OUs:
        # - show all if show_all_ou is True and user has ou_permission over all OUs or more than
        #   one,
        # - show searchable OUs
        # - show none if user has ou_permission over all OUs and show_none_ou is True
        # - when no choice is made,
        #   - show all ou is show_all_ou is True (including None if user has ou_permission over all
        #   OUs)
        #   - else show none OU
        # - when a choice is made apply it
        # if there is one OU:
        # - hide ou field
        all_ou_label = kwargs.pop('all_ou_label', pgettext('organizational unit', 'All'))
        self.queryset = kwargs.pop('queryset', None)
        self.show_all_ou = kwargs.pop('show_all_ou', app_settings.SHOW_ALL_OU)
        self.show_none_ou = kwargs.pop('show_none_ou', True)
        request = kwargs['request']
        self.ou_count = utils.get_ou_count()

        if self.ou_count > 1:
            self.search_all_ous = request.user.has_perm(self.ou_permission)
            if 'ou_queryset' in kwargs:
                self.ou_qs = kwargs.pop('ou_queryset')
            elif self.search_all_ous:
                self.ou_qs = OrganizationalUnit.objects.all()
            else:
                self.ou_qs = request.user.ous_with_perm(self.ou_permission)

            if self.queryset:
                # we were passed an explicit list of objects linked to OUs by a field named 'ou',
                # get possible OUs from this list
                related_query_name = self.queryset.model._meta.get_field('ou').related_query_name()
                objects_ou_qs = OrganizationalUnit.objects.filter(
                    **{'%s__in' % related_query_name: self.queryset}
                ).distinct()
                # to combine queryset with distinct, each queryset must have the distinct flag
                self.ou_qs = self.ou_qs.distinct() | objects_ou_qs

            # even if default ordering is by name on the model, we are not sure it's kept after the
            # ORing in the previous if condition, so we sort it again.
            self.ou_qs = self.ou_qs.order_by('name')

            # build choice list
            choices = []
            if self.show_all_ou and (len(self.ou_qs) > 1 or self.search_all_ous):
                choices.append(('all', all_ou_label))
            for ou in self.ou_qs:
                choices.append((str(ou.pk), str(ou)))
            if self.show_none_ou and self.search_all_ous:
                choices.append(('none', pgettext('organizational unit', 'None')))

            # if user does not have ou_permission over all OUs, select user OU as default selected
            # OU we must modify data as the form must always be valid
            ou_key = self.add_prefix('ou')
            data = kwargs.setdefault('data', {}).copy()
            kwargs['data'] = data
            if ou_key not in data:
                initial_ou = kwargs.get('initial', {}).get('ou')
                if initial_ou in [str(ou.pk) for ou in self.ou_qs]:
                    data[ou_key] = initial_ou
                elif self.show_all_ou and (self.search_all_ous or len(self.ou_qs) > 1):
                    data[ou_key] = 'all'
                elif request.user.ou in self.ou_qs:
                    data[ou_key] = str(request.user.ou.pk)
                else:
                    data[ou_key] = str(self.ou_qs[0].pk)

        super().__init__(*args, **kwargs)

        # modify choices after initialization
        if self.ou_count > 1:
            self.fields['ou'].choices = choices

        # if there is only one OU, we remove the field
        # if there is only one choice, we disable the field
        if self.ou_count < 2:
            del self.fields['ou']
        elif len(choices) < 2:
            self.fields['ou'].widget.attrs['disabled'] = ''

    def filter_no_ou(self, qs):
        if self.ou_count > 1:
            if self.show_all_ou:
                if self.search_all_ous:
                    return qs
                else:
                    return qs.filter(ou__in=self.ou_qs)
            else:
                qs = qs.none()
        return qs

    def clean(self):
        ou = self.cleaned_data.get('ou')
        self.cleaned_data['ou_filter'] = ou
        try:
            ou_pk = int(ou)
        except (TypeError, ValueError):
            self.cleaned_data['ou'] = None
        else:
            for ou in self.ou_qs:
                if ou.pk == ou_pk:
                    self.cleaned_data['ou'] = ou
                    break
            else:
                self.cleaned_data['ou'] = None
        return self.cleaned_data

    def filter_by_ou(self, qs):
        if self.cleaned_data.get('ou_filter'):
            ou_filter = self.cleaned_data['ou_filter']
            ou = self.cleaned_data['ou']
            if ou_filter == 'all':
                qs = self.filter_no_ou(qs)
            elif ou_filter == 'none':
                qs = qs.filter(ou__isnull=True)
            elif ou:
                qs = qs.filter(ou=ou)
        else:
            qs = self.filter_no_ou(qs)
        return qs

    def filter(self, qs):
        if hasattr(super(), 'filter'):
            qs = super().filter(qs)
        qs = self.filter_by_ou(qs)
        return qs


class RoleSearchForm(ServiceRoleSearchForm, OUSearchForm):
    ou_permission = 'a2_rbac.search_role'

    admin_roles = forms.BooleanField(label=_('Show admin roles of other roles'), required=False)


class UserRoleSearchForm(OUSearchForm, ServiceRoleSearchForm):
    ou_permission = 'a2_rbac.change_role'
    field_order = ['text', 'internals', 'limit_to_user', 'ou']

    limit_to_user = forms.BooleanField(initial=False, label=_('Show only direct user roles'), required=False)

    def __init__(self, *args, **kwargs):
        request = kwargs['request']
        self.user = kwargs.pop('user')
        role_members_from_ou = kwargs.pop('role_members_from_ou')

        if role_members_from_ou:
            assert self.user
            # limit ou to target user ou
            ou_qs = request.user.ous_with_perm(self.ou_permission).order_by('name')
            if self.user.ou_id:
                ou_qs = ou_qs.filter(id=self.user.ou_id)
            else:
                ou_qs = ou_qs.none()
            kwargs['ou_queryset'] = ou_qs
        super().__init__(*args, **kwargs)

    def filter_no_ou(self, qs):
        return qs

    def filter(self, qs):
        qs = super().filter(qs)
        if self.cleaned_data['limit_to_user']:
            qs = qs.filter(members=self.user)
        return qs


class UserSearchForm(OUSearchForm, CssClass, PrefixFormMixin, FormWithRequest):
    ou_permission = 'custom_user.search_user'
    prefix = 'search'

    text = forms.CharField(label=_('Free text'), required=False)
    only_superusers = forms.BooleanField(label=_('Only show superusers'), initial=False, required=False)

    def __init__(self, *args, **kwargs):
        self.minimum_chars = kwargs.pop('minimum_chars', 0)
        super().__init__(*args, **kwargs)

    def not_enough_chars(self):
        text = self.cleaned_data.get('text')
        return self.minimum_chars and (not text or len(text) < self.minimum_chars)

    def enough_chars(self):
        text = self.cleaned_data.get('text')
        return text and len(text) >= self.minimum_chars

    def filter(self, qs):
        qs = super().filter(qs)
        if self.cleaned_data['only_superusers']:
            qs = qs.filter(is_superuser=True)
        if self.enough_chars():
            qs = qs.free_text_search(self.cleaned_data['text'])
        elif self.not_enough_chars():
            qs = qs.none()
        return qs


class RoleMembersSearchForm(UserSearchForm):
    all_members = forms.BooleanField(initial=False, label=_('View all members'), required=False)

    def __init__(self, *args, **kwargs):
        disable_all_members = kwargs.pop('disable_all_members', False)
        super().__init__(*args, **kwargs)
        if disable_all_members:
            self.fields['all_members'].widget.attrs['disabled'] = True


class UserAddChooseOUForm(OUSearchForm):
    ou_permission = 'custom_user.add_user'


class NameSearchForm(CssClass, PrefixFormMixin, FormWithRequest):
    prefix = 'search'

    text = forms.CharField(label=_('Name'), required=False)

    def filter(self, qs):
        if self.cleaned_data.get('text'):
            qs = qs.filter(name__icontains=self.cleaned_data['text'])
        return qs


class ServiceSearchForm(OUSearchForm, NameSearchForm):
    pass


class RoleEditForm(SlugMixin, HideOUFieldMixin, LimitQuerysetFormMixin, CssClass, forms.ModelForm):
    emails = CommaSeparatedCharField(
        label=_('Emails'),
        item_validators=[EmailValidator()],
        required=False,
        help_text=_('Emails must be separated by commas.'),
    )

    class Meta:
        model = Role
        fields = ('name', 'slug', 'ou', 'description', 'details', 'emails', 'emails_to_members')
        widgets = {
            'name': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'ou' in self.fields:
            self.fields['ou'].required = True


class OUEditForm(SlugMixin, CssClass, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].label = _('label').title()
        if 'colour' in self.fields:
            self.fields['colour'].widget = forms.TextInput(attrs={'type': 'color'})

        # disable overriden local configuration
        help_text = _('This option is disabled because global unicity is set.')
        for option in ('email', 'username', 'phone'):
            if (
                getattr(a2_app_settings, 'A2_%s_IS_UNIQUE' % option.upper(), False)
                and '%s_is_unique' % option in self.fields
            ):
                self.fields['%s_is_unique' % option].disabled = True
                self.fields['%s_is_unique' % option].help_text = help_text

        if 'phone_is_unique' in self.fields and not get_password_authenticator().is_phone_authn_active:
            del self.fields['phone_is_unique']

    class Meta:
        model = OrganizationalUnit
        fields = (
            'name',
            'slug',
            'default',
            'username_is_unique',
            'email_is_unique',
            'phone_is_unique',
            'validate_emails',
            'show_username',
            'check_required_on_login_attributes',
            'user_can_reset_password',
            'user_add_password_policy',
            'clean_unused_accounts_alert',
            'clean_unused_accounts_deletion',
            'home_url',
            'logo',
            'colour',
        )


# we need a model form so that we can use a BaseEditView, a simple Form
# would not work
class UserChangeEmailForm(CssClass, FormWithRequest, forms.ModelForm):
    new_email = ValidatedEmailField(label=_('Email'))

    def __init__(self, *args, **kwargs):
        initial = kwargs.setdefault('initial', {})
        instance = kwargs.get('instance')
        if instance:
            initial['new_email'] = instance.email
        super().__init__(*args, **kwargs)

    def save(self, *args, **kwargs):
        new_email = self.cleaned_data['new_email']
        send_email_change_email(
            self.instance,
            new_email,
            request=self.request,
            template_names=['authentic2/manager/user_change_email_notification'],
        )
        return self.instance

    class Meta:
        fields = ()


class SiteImportForm(forms.Form):
    file_field_label = _('Site Export File')

    site_json = forms.FileField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site_json'].label = self.file_field_label

    def clean_site_json(self):
        try:
            return json.loads(self.cleaned_data['site_json'].read().decode())
        except ValueError:
            raise ValidationError(_('File is not in the expected JSON format.'))


class OusImportForm(SiteImportForm):
    file_field_label = _('Organizational Units Export File')


class RolesImportForm(LimitQuerysetFormMixin, SiteImportForm):
    file_field_label = _('Roles Export File')

    ou = forms.ModelChoiceField(
        required=False,
        label=_('Force organizational unit'),
        queryset=OrganizationalUnit.objects,
    )


ENCODINGS = [
    ('utf-8-sig', _('Unicode (UTF-8)')),
    ('cp1252', _('Western Europe (Windows-1252)')),
    ('iso-8859-15', _('Western Europe (ISO-8859-15)')),
]


class UserImportForm(FormWithRequest):
    import_file = forms.FileField(label=_('Import file'), help_text=_('A CSV file'))
    encoding = forms.ChoiceField(label=_('Encoding'), choices=ENCODINGS)
    ou = forms.ModelChoiceField(
        label=_('Organizational Unit'),
        queryset=OrganizationalUnit.objects.all(),
    )

    @staticmethod
    def raise_validation_error(error_message):
        message_prefix = gettext('Invalid import file')
        raise forms.ValidationError('%s : %s' % (message_prefix, str(error_message)))

    def __init__(self, *args, **kwargs):
        request = kwargs.get('request')
        super().__init__(*args, **kwargs)

        field = self.fields['ou']
        qs = DjangoRBACBackend().ous_with_perm(request.user, 'custom_user.admin_user')
        field.queryset = qs
        count = qs.count()
        if count == 1:
            field.initial = qs[0].pk
            field.empty_label = None

    def clean(self):
        from authentic2.csv_import import CsvImporter

        import_file = self.cleaned_data['import_file']
        encoding = self.cleaned_data['encoding']
        # force seek(0)
        import_file.open()
        importer = CsvImporter()
        if not importer.run(import_file, encoding):
            self.raise_validation_error(importer.error.description or importer.error.code)
        self.cleaned_data['rows_count'] = len(importer.rows)

    def save(self):
        import_file = self.cleaned_data['import_file']
        import_file.open()
        new_import = user_import.UserImport.new(
            import_file=import_file, encoding=self.cleaned_data['encoding']
        )
        UserImport.objects.get_or_create(uuid=new_import.uuid, ou=self.cleaned_data['ou'])
        with new_import.meta_update as meta:
            meta['filename'] = import_file.name
            meta['ou'] = self.cleaned_data['ou']
            meta['rows_count'] = self.cleaned_data['rows_count']
        return new_import


class RolesCsvImportForm(LimitQuerysetFormMixin, forms.Form):
    import_file = forms.FileField(
        label=_('Roles file'),
        required=True,
        help_text=_('CSV file with role name and optionnaly role slug and organizational unit.'),
    )

    ou = forms.ModelChoiceField(
        label=_('Organizational unit'),
        queryset=OrganizationalUnit.objects,
        initial=lambda: get_default_ou().pk,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if utils.get_ou_count() < 2:
            self.fields['ou'].widget = forms.HiddenInput()

    def clean(self):
        super().clean()

        content = self.cleaned_data['import_file'].read()
        if b'\0' in content:
            raise ValidationError(_('Invalid file format.'))

        for charset in ('utf-8-sig', 'iso-8859-15'):
            try:
                content = content.decode(charset)
                break
            except UnicodeDecodeError:
                continue
        # all byte-sequences are ok for iso-8859-15 so we will always reach
        # this line with content being a unicode string.

        try:
            dialect = csv.Sniffer().sniff(content)
        except csv.Error:
            dialect = None

        all_roles = Role.objects.all()
        roles_by_slugs = defaultdict(dict)
        for role in all_roles:
            roles_by_slugs[role.ou][role.slug] = role
        roles_by_names = defaultdict(dict)
        for role in all_roles:
            if role.name:
                roles_by_names[role.ou][role.name] = role

        self.roles = []
        errors = []
        for i, csvline in enumerate(csv.reader(StringIO(content), dialect=dialect, delimiter=',')):
            if not csvline:
                continue

            if i == 0:
                if csvline != ['name', 'slug', 'ou'][: len(csvline)]:
                    header = ','.join(csvline)
                    raise ValidationError(_('Invalid file header "%s", expected "name,slug,ou".') % header)
                continue

            name = csvline[0]
            if not name:
                self.add_line_error(_('Name is required.'), i)
                continue

            slug = ''
            if len(csvline) > 1 and csvline[1]:
                try:
                    validate_slug(csvline[1])
                    slug = csvline[1]
                except ValidationError:
                    self.add_line_error(_('Invalid slug "%s".') % csvline[1], i)
                    continue

            ou = self.cleaned_data['ou']
            if len(csvline) > 2 and csvline[2]:
                try:
                    ou = OrganizationalUnit.objects.get(slug=csvline[2])
                except OrganizationalUnit.DoesNotExist:
                    self.add_line_error(_('Organizational Unit %s does not exist.') % csvline[2], i)
                    continue

            if name in roles_by_names.get(ou, {}):
                role = roles_by_names[ou][name]
                role.slug = slug or role.slug
            elif slug in roles_by_slugs.get(ou, {}):
                role = roles_by_slugs[ou][slug]
                role.name = name
            else:
                role = Role(name=name, slug=slug)

            if not role.slug:
                role.slug = generate_slug(role.name, seen_slugs=roles_by_slugs[ou])

            roles_by_slugs[ou][role.slug] = role
            roles_by_names[ou][role.name] = role

            role.ou = ou
            self.roles.append(role)
        if errors:
            raise ValidationError(errors)

    def add_line_error(self, error, line):
        error = _('%(error)s (line %(number)d)') % {'error': error, 'number': line + 1}
        self.add_error('import_file', error)


class HeavySelect2WidgetNoCache(HeavySelect2Widget):
    class Media:
        js = ('authentic2/manager/js/select2_locale.js',)

    def set_to_cache(self):
        pass


class ChooseUserOrRoleForm(FormWithRequest, forms.Form):
    user_or_role = forms.CharField(label=_('Add to role'))
    action = forms.CharField(initial='add', widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.role = kwargs.pop('role', None)
        super().__init__(*args, **kwargs)
        self.fields['user_or_role'].widget = HeavySelect2WidgetNoCache(
            data_url=reverse('user-or-role-select2-json', kwargs={'pk': self.role.pk}),
            attrs={'data-placeholder': _('Select a user or a role'), 'data-minimum-input-length': 3},
        )

    def clean(self):
        super().clean()
        try:
            object_type, pk = self.cleaned_data.get('user_or_role', '').split('-')
            pk = int(pk)
        except (ValueError, TypeError):
            return

        if object_type == 'user':
            try:
                self.cleaned_data['user'] = self.get_user_queryset(self.request.user, self.role).get(pk=pk)
            except User.DoesNotExist:
                return
        elif object_type == 'role':
            try:
                self.cleaned_data['role'] = self.get_role_queryset(self.request.user, self.role).get(pk=pk)
            except Role.DoesNotExist:
                return

    @staticmethod
    def get_role_queryset(user, role):
        qs = Role.objects.exclude(pk=role.pk)

        perm = '%s.search_%s' % (Role._meta.app_label, Role._meta.model_name)
        return user.filter_by_perm(perm, qs)

    @staticmethod
    def get_user_queryset(user, role):
        qs = User.objects.all()
        if app_settings.ROLE_MEMBERS_FROM_OU and role.ou:
            qs = qs.filter(ou=role.ou)

        perm = '%s.search_%s' % (User._meta.app_label, User._meta.model_name)
        return user.filter_by_perm(perm, qs)


class APIClientForm(forms.ModelForm):
    field_order = (
        'name',
        'description',
        'identifier',
        'apiclient_password',
        'ou',
        'apiclient_roles',
        # more specific config options, would deserve appearing in a separate tab
        'allowed_ip',
        'denied_ip',
        'ip_allow_deny',
        'restrict_to_anonymised_data',
        'allowed_user_attributes',
    )
    apiclient_password = forms.CharField(
        label=_('Password'),
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}, render_value=True),
        required=True,
        help_text='<a onclick="return apiclient_random_password(45)">%s</a>' % _('Generate new password'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        core_attributes = ['first_name', 'last_name', 'email', 'username']
        # restrict to AttributeManager(disabled=False) and discard core attributes
        self.fields['allowed_user_attributes'].queryset = Attribute.objects.exclude(name__in=core_attributes)
        self.fields['allowed_user_attributes'].help_text = _(
            "Select one or multiple attributes if you want to restrict the client's access to these attributes."
        )

        if not self.instance.id:
            self.initial = {'apiclient_password': generate_apiclient_password()}

        # TODO drop temporary feature flag setting once feature is implemented
        # everywhere it's needed
        if not a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS:
            for fieldname in ('allowed_ip', 'denied_ip', 'ip_allow_deny'):
                del self.fields[fieldname]

    def clean(self):
        if self.cleaned_data['apiclient_password']:
            ret, hint = validate_apiclient_password(self.cleaned_data['apiclient_password'])
            if not ret:
                raise ValidationError(hint)
        if not self.instance.id and not self.cleaned_data['apiclient_password']:
            # When creating a new APIClient the password field is mandatory
            # it is not when updating
            raise ValidationError(
                self.fields['apiclient_password'].error_messages['required'], code='required'
            )
        ou = self.cleaned_data['ou']
        if ou:
            unauthorized_roles = self.cleaned_data['apiclient_roles'].exclude(ou=ou)
            if unauthorized_roles:
                unauthorized_roles = ', '.join(unauthorized_roles.values_list('name', flat=True))
                self.add_error(
                    'apiclient_roles',
                    _(
                        f'The following roles do not belong to organizational unit {ou.name}: {unauthorized_roles}.'
                    ),
                )
        # TODO drop temporary feature flag setting once feature is implemented
        # everywhere it's needed
        if not a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS:
            return super().clean()
        for fieldname in ('allowed_ip', 'denied_ip'):
            cleaned = []
            for ip in self._iter_ip(self.cleaned_data[fieldname]):
                if not ip or ip.startswith('#'):
                    cleaned.append(ip)
                    continue
                try:
                    net = netaddr.IPNetwork(ip)
                    if net.version == 6 and net.prefixlen == 128 or net.version == 4 and net.prefixlen == 32:
                        clean_ip = str(net.ip)
                    else:
                        clean_ip = str(net)
                    cleaned.append(clean_ip)
                except netaddr.AddrFormatError:
                    self.add_error(
                        fieldname,
                        _('Invalid IP address in list %(invalid)r') % {'invalid': ip},
                    )
            self.cleaned_data[fieldname] = '\n'.join(cleaned)
        return super().clean()

    def save(self, *args, **kwargs):
        raw_password = self.cleaned_data.pop('apiclient_password')
        if raw_password:
            self.instance.set_password(raw_password)
        if self.instance.identifier_legacy is not None:
            self.instance.identifier_legacy = None
        return super().save(*args, **kwargs)

    def _iter_ip(self, content):
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                yield line
                continue
            for ip in line.split():
                yield ip.strip()

    class Meta:
        model = APIClient
        fields = (
            'name',
            'description',
            'identifier',
            'ou',
            'allowed_ip',
            'denied_ip',
            'ip_allow_deny',
            'restrict_to_anonymised_data',
            'apiclient_roles',
            'allowed_user_attributes',
        )
        field_classes = {'apiclient_roles': ChooseRolesField}
        widgets = {'identifier': forms.TextInput(attrs={'autocomplete': 'new-password'})}
        help_texts = {
            'allowed_ip': _(
                'One IP (v4 or v6) per line, with an optionnal CIDR prefix. Lines starting with "#" are comments.'
            ),
            'denied_ip': _(
                'One IP (v4 or v6) per line, with an optionnal CIDR prefix. Lines starting with "#" are comments.'
            ),
            'ip_allow_deny': _(
                'Order for IP restrictions evaluation. '
                'Checked means allow/deny (an IP in allowed list can be denied if in denied list)'
                ', unchecked is deny/allow (denied IP can be allowed by corresponding list).'
            ),
        }


class APIClientEditForm(APIClientForm):
    apiclient_password = forms.CharField(
        label=_('New password'),
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text=format_lazy(
            '{} <a onclick="return apiclient_random_password(45)">{}</a>',
            _('The password will remain unchanged if this field is left empty.'),
            _('Generate new password'),
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        others = self.instance.__class__.objects.by_identifier(self.instance.identifier)
        if len(others) > 1 or self.instance.identifier_legacy is not None:
            self.initial['identifier'] = self.instance.identifier_legacy or self.instance.identifier
            self.fields['identifier'].value = self.initial['identifier']
            self.fields['identifier'].help_text = _('Duplicated identifier, please change it')

    def clean(self):
        cleaned_data = super().clean()
        if 'identifier' in self.changed_data and cleaned_data['identifier']:
            others = self.instance.__class__.objects.by_identifier(cleaned_data['identifier'])
            if len(others) > 0:
                raise ValidationError(_('Identifier is not unique, please change'))
        return cleaned_data

    def save(self):
        ret = super().save()
        dup_client = self.instance.__class__.objects.by_identifier(self.initial['identifier']).all()
        if len(dup_client) == 1:
            if dup_client[0].identifier_legacy is not None:
                dup_client[0].identifier_legacy = None
                dup_client[0].save()
        return ret


class ServiceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        if 'user' in kwargs:
            # OIDC services form initialization requires knowing user permissions.
            # this information isn't used for plain services yet.
            # TODO stop using a generic ServiceEditView for OIDC services(?)
            kwargs.pop('user')
        super().__init__(*args, **kwargs)

    class Meta:
        model = Service
        fields = ['name', 'slug', 'ou', 'unauthorized_url']


class ServicesSettingsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for setting in Setting.objects.filter_namespace('sso'):
            if RUNTIME_SETTINGS[setting.key]['type'] == 'url':
                field = forms.URLField
            else:
                field = forms.CharField

            self.fields[setting.key] = field(
                initial=setting.value,
                label=RUNTIME_SETTINGS[setting.key]['name'],
                required=False,
            )
            if RUNTIME_SETTINGS[setting.key]['type'] == 'colour':
                self.fields[setting.key].widget = forms.TextInput(attrs={'type': 'color'})


class UsersAdvancedConfigurationForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for setting in Setting.objects.filter_namespace('users'):
            if RUNTIME_SETTINGS[setting.key]['type'] == 'bool':
                self.fields[setting.key] = forms.BooleanField(
                    initial=setting.value,
                    label=RUNTIME_SETTINGS[setting.key]['name'],
                    required=False,
                )
            else:
                self.fields[setting.key] = forms.CharField(
                    initial=setting.value,
                    label=RUNTIME_SETTINGS[setting.key]['name'],
                    required=False,
                )

                if RUNTIME_SETTINGS[setting.key]['type'] == 'text':
                    self.fields[setting.key].widget = forms.Textarea()
