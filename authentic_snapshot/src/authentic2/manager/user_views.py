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
import collections
import datetime
import operator

from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.db import models, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.template import loader
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.html import format_html
from django.utils.timezone import now
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from django.views.generic import DetailView, FormView, TemplateView, View
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import BaseFormView

from authentic2.a2_rbac.models import OrganizationalUnit, Role, RoleParenting
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.journal.views import JournalViewWithContext
from authentic2.backends.ldap_backend import LDAPBackend
from authentic2.custom_user.backends import DjangoRBACBackend
from authentic2.models import Attribute, PasswordReset, Setting, UserImport
from authentic2.utils import hooks, spooler, switch_user
from authentic2.utils.misc import (
    get_password_authenticator,
    is_ajax,
    make_url,
    redirect,
    select_next_url,
    send_password_reset_mail,
)
from authentic2.utils.template import Template
from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient

from . import app_settings
from .forms import (
    ENCODINGS,
    ChooseUserAuthorizationsForm,
    ChooseUserRoleForm,
    UserAddChooseOUForm,
    UserAddForm,
    UserChangeEmailForm,
    UserChangePasswordForm,
    UserEditForm,
    UserImportForm,
    UserRoleSearchForm,
    UsersAdvancedConfigurationForm,
    UserSearchForm,
)
from .journal_views import BaseJournalView
from .tables import OuUserRolesTable, UserAuthorizationsTable, UserRolesTable, UserTable
from .user_export import UserExport
from .utils import get_ou_count, has_show_username
from .views import (
    Action,
    ActionMixin,
    BaseAddView,
    BaseDeleteView,
    BaseDetailView,
    BaseEditView,
    BaseSubTableView,
    BaseTableView,
    ExportMixin,
    FormNeedsRequest,
    HideOUColumnMixin,
    MediaMixin,
    OtherActionsMixin,
    PermissionMixin,
    TitleMixin,
)

User = get_user_model()


class UsersView(HideOUColumnMixin, BaseTableView):
    template_name = 'authentic2/manager/users.html'
    model = get_user_model()
    table_class = UserTable
    permissions = ['custom_user.search_user']
    search_form_class = UserSearchForm
    title = _('Users')

    def is_ou_specified(self):
        return self.search_form.is_valid() and self.search_form.cleaned_data.get('ou')

    def get_queryset(self):
        qs = super().get_queryset()
        qs = qs.select_related('ou')
        qs = qs.prefetch_related('roles', 'roles__parent_relation__parent')
        return qs

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['minimum_chars'] = app_settings.USER_SEARCH_MINIMUM_CHARS
        return kwargs

    def filter_by_search(self, qs):
        qs = super().filter_by_search(qs)
        if not self.search_form.is_valid():
            qs = qs.filter(ou=self.request.user.ou)
        return qs

    def get_table(self, **kwargs):
        show_username = has_show_username()
        if not show_username and self.is_ou_specified():
            show_username = self.is_ou_specified().show_username
        if not show_username:
            exclude = kwargs.setdefault('exclude', [])
            if 'username' not in exclude:
                exclude.append('username')
        table = super().get_table(**kwargs)
        if self.search_form.not_enough_chars():
            user_qs = self.search_form.filter_by_ou(self.get_queryset())
            table.empty_text = _('Enter at least %(limit)d characters (%(user_count)d users)') % {
                'limit': self.search_form.minimum_chars,
                'user_count': user_qs.count(),
            }

        return table

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data()
        if get_ou_count() < 2:
            ou = get_default_ou()
        else:
            ou = self.search_form.cleaned_data.get('ou')
        if ou:
            if self.request.user.has_ou_perm('custom_user.add_user', ou):
                ctx['add_ou'] = ou
            else:
                self.can_add = False
        extra_actions = ctx['extra_actions'] = []
        if self.can_add:
            extra_actions.extend(
                [
                    {
                        'url': reverse('a2-manager-users-imports'),
                        'label': _('Import users'),
                    },
                ]
            )
        if self.request.user.has_perm('custom_user.admin_user'):
            extra_actions.extend(
                [
                    {
                        'url': reverse('a2-manager-users-advanced-configuration'),
                        'label': _('Users management advanced configuration'),
                        'popup': True,
                    },
                ]
            )
        return ctx


users = UsersView.as_view()


class UserAddView(ActionMixin, BaseAddView):
    model = get_user_model()
    title = _('Create user')
    action = _('Create')
    fields = [
        'username',
        'first_name',
        'last_name',
        'email',
        'generate_password',
        'password1',
        'password2',
        'reset_password_at_next_login',
        'send_mail',
    ]
    form_class = UserAddForm
    permissions = ['custom_user.add_user']
    template_name = 'authentic2/manager/user_add.html'
    duplicate_users = None

    def dispatch(self, request, *args, **kwargs):
        qs = request.user.ous_with_perm('custom_user.add_user')
        try:
            self.ou = qs.get(pk=self.kwargs['ou_pk'])
        except OrganizationalUnit.DoesNotExist:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['ou'] = self.ou
        return kwargs

    def get_fields(self):
        fields = list(self.fields)
        if not self.ou.show_username:
            fields.remove('username')
        i = fields.index('generate_password')
        if self.request.user.is_superuser and 'is_superuser' not in self.fields:
            fields.insert(i, 'is_superuser')
            i += 1
        for attribute in Attribute.objects.all():
            fields.insert(i, attribute.name)
            i += 1
        return fields

    def get_success_url(self):
        return select_next_url(
            self.request,
            default=reverse('a2-manager-user-detail', kwargs={'pk': self.object.pk}),
            include_post=True,
            replace={
                '$UUID': self.object.uuid,
            },
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = select_next_url(self.request, default='../..', field_name='cancel')
        context['next'] = select_next_url(self.request, default=None, include_post=True)
        context['ou'] = self.ou
        context['duplicate_users'] = self.duplicate_users
        return context

    def form_valid(self, form):
        if app_settings.CHECK_DUPLICATE_USERS:
            first_name = form.cleaned_data['first_name']
            last_name = form.cleaned_data['last_name']
            duplicate_users = User.objects.find_duplicates(
                first_name=first_name,
                last_name=last_name,
                birthdate=form.cleaned_data.get('birthdate'),
            )
            token = self.request.POST.get('confirm-creation-token')
            valid_confirmation_token = bool(token == '%s %s' % (first_name, last_name))
            if duplicate_users and not valid_confirmation_token:
                self.duplicate_users = duplicate_users
                return self.form_invalid(form)

        response = super().form_valid(form)
        hooks.call_hooks(
            'event', name='manager-add-user', user=self.request.user, instance=form.instance, form=form
        )
        self.request.journal.record('manager.user.creation', form=form)
        return response

    def get_initial(self, *args, **kwargs):
        initial = super().get_initial(*args, **kwargs)
        initial.update(self.get_user_add_policies())
        return initial

    def get_user_add_policies(self, *args, **kwargs):
        ou = OrganizationalUnit.objects.get(pk=self.kwargs['ou_pk'])
        value = ou.user_add_password_policy
        return ou.USER_ADD_PASSWD_POLICY_VALUES[value]._asdict()


user_add = UserAddView.as_view()


def user_add_default_ou(request):
    ou = get_default_ou()
    return redirect(request, 'a2-manager-user-add', kwargs={'ou_pk': ou.id}, keep_params=True)


class UserAddChooseOU(TitleMixin, FormNeedsRequest, FormView):
    template_name = 'authentic2/manager/form.html'
    title = _('Choose organizational unit in which to create user')
    form_class = UserAddChooseOUForm

    def get_success_url(self):
        return reverse('a2-manager-user-add', kwargs={'ou_pk': self.ou_pk})

    def form_valid(self, form):
        self.ou_pk = form.cleaned_data['ou'].pk
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['show_all_ou'] = False
        kwargs['show_none_ou'] = False
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = _('Validate')
        return context


user_add_choose_ou = UserAddChooseOU.as_view()


class UserDetailView(OtherActionsMixin, BaseDetailView):
    model = get_user_model()
    fields = ['username', 'ou', 'first_name', 'last_name', 'email']
    form_class = UserEditForm
    template_name = 'authentic2/manager/user_detail.html'
    slug_field = 'uuid'

    @property
    def title(self):
        return self.object.get_full_name()

    @property
    def is_oidc_services(self):
        return OIDCClient.objects.exists()

    def get_other_actions(self):
        yield from super().get_other_actions()
        yield Action('password_reset', _('Reset password'), permission='custom_user.reset_password_user')
        if self.object.is_active:
            yield Action('deactivate', _('Suspend'), permission='custom_user.activate_user')
        else:
            yield Action('activate', _('Activate'), permission='custom_user.activate_user')
        if PasswordReset.objects.filter(user=self.object).exists():
            yield Action(
                'delete_password_reset',
                _('Do not force password change on next login'),
                permission='custom_user.reset_password_user',
            )
        else:
            yield Action(
                'force_password_change',
                _('Force password change on next login'),
                permission='custom_user.reset_password_user',
            )
        yield Action(
            'change_password',
            _('Change user password'),
            url_name='a2-manager-user-change-password',
            permission='custom_user.change_password_user',
        )
        if self.request.user.is_superuser:
            yield Action('su', _('Impersonate this user'), url_name='a2-manager-user-su')
        if self.object.ou and self.object.ou.validate_emails:
            yield Action(
                'change_email',
                _('Change user email'),
                url_name='a2-manager-user-change-email',
                permission='custom_user.change_email_user',
            )

    def action_force_password_change(self, request, *args, **kwargs):
        PasswordReset.objects.get_or_create(user=self.object)
        request.journal.record('manager.user.password.change.force', target_user=self.object)

    def action_activate(self, request, *args, **kwargs):
        self.object.is_active = True
        self.object.save()
        request.journal.record('manager.user.activation', target_user=self.object)

    def action_deactivate(self, request, *args, **kwargs):
        if request.user == self.object:
            messages.warning(request, _('You cannot desactivate your own user'))
        else:
            self.object.mark_as_inactive(reason=_('by %s') % request.user)
            request.journal.record('manager.user.deactivation', target_user=self.object)

    def action_password_reset(self, request, *args, **kwargs):
        user = self.object
        if not user.email:
            messages.info(
                request,
                _('User has no email, it\'not possible to send him am email to reset its password'),
            )
            return
        send_password_reset_mail(user, request=request)
        messages.info(request, _('A mail was sent to %s') % self.object.email)
        request.journal.record('manager.user.password.reset.request', target_user=self.object)

    def action_delete_password_reset(self, request, *args, **kwargs):
        PasswordReset.objects.filter(user=self.object).delete()
        request.journal.record('manager.user.password.change.unforce', target_user=self.object)

    def action_su(self, request, *args, **kwargs):
        return redirect(
            request, 'auth_logout', params={REDIRECT_FIELD_NAME: switch_user.build_url(self.object)}
        )

    # Copied from PasswordResetForm implementation
    def send_mail(self, subject_template_name, email_template_name, context, to_email):
        """
        Sends a django.core.mail.EmailMultiAlternatives to `to_email`.
        """
        subject = loader.render_to_string(subject_template_name, context)
        # Email subject *must not* contain newlines
        subject = ''.join(subject.splitlines())
        body = loader.render_to_string(email_template_name, context)

        email_message = EmailMultiAlternatives(subject, body, to=[to_email])
        email_message.send()

    def get_fields(self):
        fields = list(self.fields)
        if not self.object.username and self.object.ou and not self.object.ou.show_username:
            fields.remove('username')
        for attribute in Attribute.objects.all():
            if attribute.name == 'address_autocomplete':
                continue
            fields.append(attribute.name)
        if self.request.user.is_superuser and 'is_superuser' not in self.fields:
            fields.append('is_superuser')
        return fields

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        if 'email' in form.fields:
            if self.object.email_verified:
                comment = _('Email verified')
            else:
                comment = _('Email not verified')
            form.fields['email'].help_text = format_html('<b>{0}</b>', comment)
        return form

    @classmethod
    def has_perm_on_roles(cls, user, instance):
        role_qs = Role.objects.all()
        if app_settings.ROLE_MEMBERS_FROM_OU and instance.ou:
            role_qs = role_qs.filter(ou=instance.ou)
        return user.filter_by_perm('a2_rbac.manage_members_role', role_qs).exists()

    def render_sidebar_advanced_info(self, context=None):
        value = ''
        if setting := Setting.objects.filter(key='users:backoffice_sidebar_template').first():
            value = setting.value
            if value and ('{{' in value or '{%' in value):
                template = Template(value)
                value = template.render(context=context, request=self.request)
        return value

    def get_context_data(self, **kwargs):
        kwargs['default_ou'] = get_default_ou
        roles = self.object.roles_and_parents().order_by('ou__name', 'name')
        role_qs = Role.objects.all()
        if app_settings.ROLE_MEMBERS_FROM_OU and self.object.ou:
            role_qs = role_qs.filter(ou=self.object.ou)
        visible_roles = self.request.user.filter_by_perm('a2_rbac.view_role', role_qs)
        roles_by_ou = collections.OrderedDict()
        for role in roles:
            role.user_visible = bool(role in visible_roles)
            roles_by_ou.setdefault(role.ou.name if role.ou else '', []).append(role)
        kwargs['roles'] = roles
        kwargs['roles_by_ou'] = roles_by_ou
        kwargs['have_roles_on_multiple_ou'] = len(roles_by_ou.keys()) > 1
        # show modify roles button only if something is possible
        kwargs['can_change_roles'] = self.has_perm_on_roles(self.request.user, self.object)
        user_data = []
        user_data += [
            data for datas in hooks.call_hooks('manager_user_data', self, self.object) for data in datas
        ]
        kwargs['user_data'] = user_data

        realms = [block['realm'] for block in LDAPBackend.get_config() if block.get('realm')]
        # user is active and belongs to an OU that defines deletion delays
        if (
            self.object.is_active
            and self.object.email
            and self.object.ou
            and self.object.ou.clean_unused_accounts_alert
            and self.object.ou.clean_unused_accounts_deletion
        ):
            # user does not have any external identifier that would prohibit automated deletion
            if not (
                getattr(self.object, 'oidc_account', None)
                or (getattr(self.object, 'saml_identifiers', None) and self.object.saml_identifiers.all())
                or (
                    self.object.userexternalid_set.exists()
                    and any(uid.source in realms for uid in self.object.userexternalid_set.all())
                )
            ):
                # base value for computing alert & deletion is user's last login or creation date
                start = self.object.last_login or self.object.date_joined
                # but the keepalive alert date is more relevant if more recent
                if self.object.keepalive and self.object.keepalive > start:
                    start = self.object.keepalive
                kwargs['now'] = now().date()
                kwargs['alert_date'] = start.date() + datetime.timedelta(
                    days=self.object.ou.clean_unused_accounts_alert
                )
                kwargs['deletion_date'] = start.date() + datetime.timedelta(
                    days=self.object.ou.clean_unused_accounts_deletion
                )
        ctx = super().get_context_data(**kwargs)
        ctx['sidebar_advanced_info'] = self.render_sidebar_advanced_info(ctx)
        return ctx


user_detail = UserDetailView.as_view()


class UserEditView(OtherActionsMixin, ActionMixin, BaseEditView):
    model = get_user_model()
    template_name = 'authentic2/manager/user_edit.html'
    form_class = UserEditForm
    permissions = ['custom_user.change_user']
    fields = ['username', 'ou', 'first_name', 'last_name']
    slug_field = 'uuid'
    action = _('Change')
    title = _('Edit user')

    def get_fields(self):
        fields = list(self.fields)
        if not self.object.username and self.object.ou and not self.object.ou.show_username:
            fields.remove('username')
        if not self.object.ou or not self.object.ou.validate_emails:
            fields.append('email')
        for attribute in Attribute.objects.all():
            fields.append(attribute.name)
        if self.request.user.is_superuser and 'is_superuser' not in self.fields:
            fields.append('is_superuser')
        return fields

    def _get_next_url(self):
        return select_next_url(
            self.request,
            default=reverse('a2-manager-user-detail', kwargs={'pk': self.object.pk}),
            include_post=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = self._get_next_url()
        context['next'] = next_url
        context['cancel_url'] = next_url
        return context

    def get_success_url(self):
        return self._get_next_url()

    def form_valid(self, form):
        changed = False
        if 'email' in form.changed_data:
            self.object.set_email_verified(False)
            changed = True
        authenticator = get_password_authenticator()
        if (
            authenticator.phone_identifier_field
            and authenticator.phone_identifier_field.name in form.changed_data
        ):
            self.object.phone_verified_on = None
            changed = True
        if changed:
            self.object.save()
        response = super().form_valid(form)
        if form.has_changed():
            hooks.call_hooks(
                'event', name='manager-edit-user', user=self.request.user, instance=form.instance, form=form
            )
            self.request.journal.record('manager.user.profile.edit', form=form)
        return response


user_edit = UserEditView.as_view()


class UsersExportView(UsersView):
    permissions = ['custom_user.view_user']
    export_prefix = 'users-'

    def get(self, request, *args, **kwargs):
        export = UserExport.new()
        query = self.get_table_data().query
        spooler.export_users(uuid=export.uuid, query=query)
        return redirect(request, 'a2-manager-users-export-progress', kwargs={'uuid': export.uuid})


users_export = UsersExportView.as_view()


class UsersExportFileView(ExportMixin, PermissionMixin, View):
    permissions = ['custom_user.view_user']

    def get(self, request, *args, **kwargs):
        self.export = UserExport(kwargs.get('uuid'))
        if not self.export.exists:
            raise Http404()
        response = HttpResponse(self.export.csv, content_type='text/csv')
        filename = 'users-%s.csv' % timezone.now().strftime('%Y%m%d_%H%M%S')
        response['Content-Disposition'] = 'attachment; filename="%s"' % filename
        return response


users_export_file = UsersExportFileView.as_view()


class UsersExportProgressView(MediaMixin, TemplateView):
    template_name = 'authentic2/manager/user_export.html'

    def get(self, request, *args, **kwargs):
        self.uuid = kwargs.get('uuid')
        export = UserExport(self.uuid)
        if not export.exists:
            raise Http404()

        if is_ajax(request):
            return HttpResponse(export.progress)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['uuid'] = self.uuid
        return ctx


users_export_progress = UsersExportProgressView.as_view()


class UserChangePasswordView(ActionMixin, BaseEditView):
    template_name = 'authentic2/manager/form.html'
    model = get_user_model()
    form_class = UserChangePasswordForm
    permissions = ['custom_user.change_password_user']
    title = _('Change user password')
    action = _('Submit')
    success_url = '..'
    slug_field = 'uuid'

    def get_success_message(self, cleaned_data):
        if cleaned_data.get('send_mail'):
            return gettext('New password sent to %s') % self.object.email
        else:
            return gettext('New password set')

    def form_valid(self, form):
        response = super().form_valid(form)
        hooks.call_hooks(
            'event', name='manager-change-password', user=self.request.user, instance=form.instance, form=form
        )
        self.request.journal.record('manager.user.password.change', form=form)
        return response


user_change_password = UserChangePasswordView.as_view()


class UserChangeEmailView(BaseEditView):
    template_name = 'authentic2/manager/user_change_email.html'
    model = get_user_model()
    form_class = UserChangeEmailForm
    permissions = ['custom_user.change_email_user']
    success_url = '..'
    slug_field = 'uuid'
    title = _('Change user email')

    def get_success_message(self, cleaned_data):
        if cleaned_data['new_email'] != self.object.email:
            return gettext('A mail was sent to %s to verify it.') % cleaned_data['new_email']
        return None

    def form_valid(self, form):
        response = super().form_valid(form)
        new_email = form.cleaned_data['new_email']
        hooks.call_hooks(
            'event',
            name='manager-change-email-request',
            user=self.request.user,
            instance=form.instance,
            form=form,
            email=new_email,
        )
        return response


user_change_email = UserChangeEmailView.as_view()


class UserRolesView(HideOUColumnMixin, BaseSubTableView):
    model = get_user_model()
    form_class = ChooseUserRoleForm
    search_form_class = UserRoleSearchForm
    success_url = '.'
    slug_field = 'uuid'

    @property
    def template_name(self):
        if self.is_ou_specified():
            return 'authentic2/manager/user_ou_roles.html'
        else:
            return 'authentic2/manager/user_roles.html'

    @property
    def table_class(self):
        if self.is_ou_specified():
            return OuUserRolesTable
        else:
            return UserRolesTable

    def is_ou_specified(self):
        '''Differentiate view of all user's roles from view of roles by OU'''
        return self.search_form.is_valid() and self.search_form.cleaned_data.get('ou_filter') != 'all'

    def get_table_queryset(self):
        if self.is_ou_specified():
            roles = self.object.roles.all()
            rp_qs = RoleParenting.alive.filter(child__in=roles)
            qs = Role.objects.all()
            qs = qs.prefetch_related(models.Prefetch('child_relation', queryset=rp_qs, to_attr='via'))
            qs = qs.annotate(
                member=models.Exists(
                    Role.members.through.objects.filter(role_id=models.OuterRef('pk'), user_id=self.object.pk)
                )
            )
            qs2 = self.request.user.filter_by_perm('a2_rbac.manage_members_role', qs)
            managable_ids = [str(pk) for pk in qs2.values_list('pk', flat=True)]
            qs = qs.extra(select={'has_perm': 'a2_rbac_role.id in (%s)' % ', '.join(managable_ids)})
            qs = qs.exclude(slug__startswith='_a2-managers-of-role')
            return qs
        else:
            return self.object.roles_and_parents()

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)  # pylint: disable=assignment-from-no-return
        if response is not None:
            return response
        if not UserDetailView.has_perm_on_roles(request.user, self.object):
            return redirect(request, 'a2-manager-user-detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        user = self.object
        role = form.cleaned_data['role']
        action = form.cleaned_data['action']
        if action == 'add':
            if user.roles.filter(pk=role.pk):
                messages.warning(
                    self.request, _('User {user} has already the role {role}.').format(user=user, role=role)
                )
            else:
                user.roles.add(role)
                hooks.call_hooks(
                    'event', name='manager-add-role-member', user=self.request.user, role=role, member=user
                )
                self.request.journal.record('manager.role.membership.grant', member=user, role=role)
        elif action == 'remove':
            if user.roles.filter(pk=role.pk).exists():
                user.roles.remove(role)
                hooks.call_hooks(
                    'event', name='manager-remove-role-member', user=self.request.user, role=role, member=user
                )
                self.request.journal.record('manager.role.membership.removal', member=user, role=role)
        return super().form_valid(form)

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['all_ou_label'] = ''
        kwargs['user'] = self.object
        kwargs['role_members_from_ou'] = app_settings.ROLE_MEMBERS_FROM_OU
        kwargs['queryset'] = self.request.user.filter_by_perm('a2_rbac.view_role', Role.objects.all())
        if self.object.ou_id:
            initial = kwargs.setdefault('initial', {})
            initial['ou'] = str(self.object.ou_id)
        return kwargs

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # if role members can only be from the same OU, we filter roles based on the user's ou
        if app_settings.ROLE_MEMBERS_FROM_OU and self.object.ou_id:
            kwargs['ou'] = self.object.ou
        return kwargs


roles = UserRolesView.as_view()


class UserDeleteView(BaseDeleteView):
    model = get_user_model()
    title = _('Delete user')
    template_name = 'authentic2/manager/user_delete.html'
    success_url = reverse_lazy('a2-manager-users')

    def form_valid(self, *args, **kwargs):
        return self.delete_action(super().form_valid, *args, **kwargs)

    @transaction.atomic
    def delete_action(self, callback, *args, **kwargs):
        self.request.journal.record('manager.user.deletion', target_user=self.object)
        response = callback(*args, **kwargs)
        hooks.call_hooks('event', name='manager-delete-user', user=self.request.user, instance=self.object)
        return response


user_delete = UserDeleteView.as_view()


class UserImportsView(MediaMixin, PermissionMixin, FormNeedsRequest, FormView):
    form_class = UserImportForm
    permissions = ['custom_user.admin_user']
    template_name = 'authentic2/manager/user_imports.html'

    def post(self, request, *args, **kwargs):
        if 'delete' in request.POST:
            uuid = request.POST['delete']
            obj = get_object_or_404(UserImport, uuid=uuid)
            if request.user.has_ou_perm('custom_user.admin_user', obj.ou):
                obj.user_import.delete()
                obj.delete()
                return redirect(self.request, 'a2-manager-users-imports')
            else:
                raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        user_import = form.save()
        with user_import.meta_update as meta:
            meta['user'] = self.request.user.get_full_name()
            meta['user_pk'] = self.request.user.pk
        return redirect(self.request, 'a2-manager-users-import', kwargs={'uuid': user_import.uuid})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        if self.request.user.is_superuser:
            imports = UserImport.objects.all()
        else:
            ous = DjangoRBACBackend().ous_with_perm(self.request.user, 'custom_user.admin_user')
            imports = UserImport.objects.filter(ou__id__in=ous)
        ctx['imports'] = [obj.user_import for obj in imports]
        help_columns = []
        field_columns = ['username', 'email', 'first_name', 'last_name']
        key = 'username'
        if not has_show_username():
            field_columns.remove('username')
            key = 'email'
        for field_column in field_columns:
            field = User._meta.get_field(field_column)
            if Attribute.objects.filter(name=field.name).exists():
                continue
            help_columns.append(
                {
                    'label': field.verbose_name,
                    'name': field.name,
                    'key': field.name == key,
                }
            )
        for attribute in Attribute.objects.all():
            kind = attribute.get_kind()
            if not kind.get('csv_importable', True):
                continue
            help_columns.append(
                {
                    'label': attribute.label,
                    'name': attribute.name,
                    'key': attribute.name == key,
                }
            )
        help_columns.append(
            {
                'label': _('Password hash'),
                'name': 'password_hash',
                'key': False,
            }
        )
        ctx['help_columns'] = help_columns
        example_data = (
            ','.join(column['name'] + (' key' if column['key'] else '') for column in help_columns) + '\n'
        )
        example_url = 'data:text/csv;base64,%s' % base64.b64encode(example_data.encode('utf-8')).decode(
            'ascii'
        )
        ctx['form'].fields['import_file'].help_text = format_html(
            _('{0}. {1} <a download="{3}" href="{2}">{3}</a>'),
            ctx['form'].fields['import_file'].help_text,
            _('ex.:'),
            example_url,
            _('users.csv'),
        )
        return ctx


user_imports = UserImportsView.as_view()


class UserImportView(MediaMixin, PermissionMixin, TemplateView):
    permissions = ['custom_user.admin_user']
    template_name = 'authentic2/manager/user_import.html'

    def dispatch(self, request, uuid, **kwargs):
        self.object = get_object_or_404(UserImport, uuid=uuid)

        if not request.user.has_ou_perm('custom_user.admin_user', self.object.ou):
            raise PermissionDenied

        self.user_import = self.object.user_import
        if not self.user_import.exists():
            raise Http404
        if self.user_import.encoding == 'utf-8':
            with self.user_import.meta_update as meta:
                meta['encoding'] = 'utf-8-sig'

        return super().dispatch(request, uuid, **kwargs)

    def get(self, request, uuid, filename=None):
        if filename:
            return FileResponse(self.user_import.import_file, content_type='text/csv')
        return super().get(request, uuid=uuid, filename=filename)

    def post(self, request, *args, **kwargs):
        if 'delete' in request.POST:
            uuid = request.POST['delete']
            try:
                report = self.user_import.reports[uuid]
            except KeyError:
                pass
            else:
                report.delete()
            return redirect(request, 'a2-manager-users-import', kwargs={'uuid': self.user_import.uuid})

        report = self.user_import.new_report()
        report.run(simulate=bool('simulate' in request.POST), user=request.user)
        return redirect(request, 'a2-manager-users-import', kwargs={'uuid': self.user_import.uuid})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['encoding'] = [encoding for id, encoding in ENCODINGS if id == self.user_import.encoding][0]
        ctx['user_import'] = self.user_import
        ctx['reports'] = sorted(self.user_import.reports, key=operator.attrgetter('created'), reverse=True)
        return ctx


user_import = UserImportView.as_view()


class UserImportReportView(MediaMixin, PermissionMixin, TemplateView):
    permissions = ['custom_user.admin_user']

    def dispatch(self, request, import_uuid, report_uuid):
        self.object = get_object_or_404(UserImport, uuid=import_uuid)
        if not request.user.has_ou_perm('custom_user.admin_user', self.object.ou):
            raise PermissionDenied

        self.user_import = self.object.user_import
        if not self.user_import.exists():
            raise Http404

        try:
            self.report = self.user_import.reports[report_uuid]
        except KeyError:
            raise Http404
        return super().dispatch(request, import_uuid, report_uuid)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['user_import'] = self.user_import
        ctx['report'] = self.report
        if self.report.simulate:
            ctx['report_title'] = _('Simulation')
        else:
            ctx['report_title'] = _('Execution')
        return ctx

    def get_template_names(self):
        if is_ajax(self.request):
            return ['authentic2/manager/user_import_report_row.html']
        return ['authentic2/manager/user_import_report.html']


user_import_report = UserImportReportView.as_view()


def me(request):
    if request.user.has_perm('custom_user.change_user', request.user):
        return redirect(request, 'a2-manager-user-detail', kwargs={'pk': request.user.pk})
    else:
        return redirect(request, 'account_management')


class UserSuView(MediaMixin, TitleMixin, PermissionMixin, DetailView):
    model = User
    template_name = 'authentic2/manager/user_su.html'
    title = _('Switch user')
    duration = 30  # seconds

    class Media:
        js = ('authentic2/js/js_seconds_until.js',)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        as_user = self.get_object()
        request.journal.record('user.su_token_generation', as_username=as_user.username, as_userid=as_user.id)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['su_url'] = make_url(
            'auth_logout',
            params={REDIRECT_FIELD_NAME: switch_user.build_url(self.object, self.duration)},
            request=self.request,
            absolute=True,
        )
        ctx['duration'] = self.duration
        return ctx


su = UserSuView.as_view()


class UserAuthorizationsView(
    FormNeedsRequest, BaseFormView, SingleObjectMixin, BaseTableView, PermissionMixin
):
    permissions = ['custom_user.view_user']
    template_name = 'authentic2/manager/user_authorizations.html'
    title = pgettext_lazy('manager', 'Consent Management')
    model = get_user_model()
    table_class = UserAuthorizationsTable
    form_class = ChooseUserAuthorizationsForm
    success_url = '.'
    object_list = None

    @property
    def can_manage_authorizations(self):
        return self.request.user.has_perm('custom_user.manage_authorizations_user', self.get_object())

    def get_table_data(self):
        qs = OIDCAuthorization.objects.filter(user=self.get_object())
        return qs

    def form_valid(self, form):
        response = super().form_valid(form)
        auth_id = form.cleaned_data['authorization']
        if self.can_manage_authorizations:
            qs = OIDCAuthorization.objects.filter(user=self.get_object())
            qs = qs.filter(id=auth_id.pk)
            oidc_authorization = qs.first()
            count, dummy = qs.delete()
            if count:
                self.request.journal.record(
                    'manager.user.sso.authorization.deletion',
                    service=oidc_authorization.client,
                    target_user=self.object,
                )
        return response


user_authorizations = UserAuthorizationsView.as_view()


class UserJournal(PermissionMixin, JournalViewWithContext, BaseJournalView):
    template_name = 'authentic2/manager/user_journal.html'
    permissions = ['custom_user.view_user']
    title = _('Journal')

    @cached_property
    def context(self):
        return get_object_or_404(User, pk=self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object'] = self.context
        ctx['object_name'] = self.context.get_full_name()
        return ctx


user_journal = UserJournal.as_view()


class UsersAdvancedConfigurationView(FormView):
    template_name = 'authentic2/manager/users_advanced_configuration.html'
    form_class = UsersAdvancedConfigurationForm
    title = _('Edit users management advanced configuration')
    success_url = '..'
    permissions = ['authentic2.change_user']

    def form_valid(self, form):
        for key, value in form.cleaned_data.items():
            try:
                setting = Setting.objects.get(key=key)
            except Setting.DoesNotExist:
                continue
            setting.value = value
            setting.save()
        return super().form_valid(form)


users_advanced_configuration_view = UsersAdvancedConfigurationView.as_view()
