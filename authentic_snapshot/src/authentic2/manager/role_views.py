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

import json
from functools import reduce

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import BadRequest, PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, F, Prefetch, Q, Value
from django.db.models.functions import Cast
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, TemplateView
from django.views.generic.detail import SingleObjectMixin

from authentic2 import data_transfer
from authentic2.a2_rbac.models import OrganizationalUnit, Permission, Role, RoleParenting
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.journal.views import JournalViewWithContext
from authentic2.forms.profile import modelform_factory
from authentic2.role_summary import get_roles_summary_cache
from authentic2.utils import crypto, hooks
from authentic2.utils.misc import redirect

from . import forms, resources, tables, views
from .journal_views import BaseJournalView
from .utils import has_show_username, label_from_role, label_from_user

User = get_user_model()


class RolesMixin:
    service_roles = True
    admin_roles = False

    def get_queryset(self):
        qs = super().get_queryset()
        qs = qs.select_related('ou')
        permission_ct = ContentType.objects.get_for_model(Permission)
        ct_ct = ContentType.objects.get_for_model(ContentType)
        ou_ct = ContentType.objects.get_for_model(OrganizationalUnit)
        permission_qs = Permission.objects.filter(target_ct_id__in=[ct_ct.id, ou_ct.id]).values_list(
            'id', flat=True
        )
        # only non role-admin roles, they are accessed through the
        # RoleManager views
        if not self.admin_roles:
            qs = qs.filter(
                Q(admin_scope_ct__isnull=True)
                | Q(admin_scope_ct=permission_ct, admin_scope_id__in=permission_qs)
            )
        if not self.service_roles:
            qs = qs.filter(service__isnull=True)
        return qs


class RolesView(views.SearchOUMixin, views.HideOUColumnMixin, RolesMixin, views.BaseTableView):
    template_name = 'authentic2/manager/roles.html'
    model = Role
    table_class = tables.RoleTable
    search_form_class = forms.RoleSearchForm
    permissions = ['a2_rbac.search_role']
    title = _('Roles')

    def get_queryset(self):
        qs = super().get_queryset()
        qs = qs.annotate(member_count=Count('members'))
        return qs

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['queryset'] = self.get_queryset()
        return kwargs


listing = RolesView.as_view()


class RoleAddView(views.BaseAddView):
    template_name = 'authentic2/manager/role_add.html'
    model = Role
    title = _('Add role')
    success_view_name = 'a2-manager-role-members'
    exclude_fields = ('slug',)

    def get_initial(self):
        initial = super().get_initial()
        search_ou = self.request.GET.get('search-ou')
        initial['ou'] = search_ou or get_default_ou()
        return initial

    def get_form_class(self):
        form = forms.RoleEditForm
        fields = [x for x in form.base_fields.keys() if x not in self.exclude_fields]
        return modelform_factory(self.model, form=form, fields=fields)

    def form_valid(self, form):
        response = super().form_valid(form)
        hooks.call_hooks(
            'event', name='manager-add-role', user=self.request.user, instance=form.instance, form=form
        )
        self.request.journal.record('manager.role.creation', role=form.instance)
        return response


add = RoleAddView.as_view()


class RolesExportView(views.ExportMixin, RolesView):
    resource_class = resources.RoleResource
    export_prefix = 'roles-export-'

    def get(self, request, *args, **kwargs):
        export_format = kwargs['format'].lower()
        if export_format == 'json':
            export = data_transfer.export_site(
                data_transfer.ExportContext(
                    role_qs=self.get_table_data(), export_roles=True, export_ous=False
                )
            )
            return self.export_response(json.dumps(export, indent=4), 'application/json', 'json')
        return super().get(request, *args, **kwargs)


export = RolesExportView.as_view()


class RoleViewMixin(RolesMixin):
    model = Role

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['ou'] = self.get_object().ou
        return ctx


class RoleEditView(RoleViewMixin, views.BaseEditView):
    template_name = 'authentic2/manager/role_edit.html'
    title = _('Edit role description')

    def get_form_class(self):
        return forms.RoleEditForm

    def form_valid(self, form):
        response = super().form_valid(form)
        hooks.call_hooks(
            'event', name='manager-edit-role', user=self.request.user, instance=form.instance, form=form
        )
        self.request.journal.record('manager.role.edit', role=form.instance, form=form)
        return response


edit = RoleEditView.as_view()


class RoleMembersView(views.HideOUColumnMixin, RoleViewMixin, views.BaseSubTableView):
    template_name = 'authentic2/manager/role_members.html'
    form_class = forms.ChooseUserOrRoleForm
    success_url = '.'
    search_form_class = forms.RoleMembersSearchForm
    permissions = ['a2_rbac.view_role']
    slug_field = 'uuid'
    admin_roles = True

    @property
    def table_class(self):
        if self.view_all_members:
            return tables.RoleMembersTable
        return tables.MixedUserRoleTable

    @property
    def title(self):
        return self.get_instance_name()

    @cached_property
    def children(self):
        children = self.object.children(include_self=False, annotate=True)
        if self.can_manage_members:
            return children
        return views.filter_view(self.request, children)

    def get_table_data(self):
        if self.view_all_members:
            return super().get_table_data()
        members = views.filter_view(self.request, self.object.members.all())
        members = members.annotate(direct=Value(True, output_field=BooleanField()))
        members = self.filter_by_search(members)
        return list(self.children) + list(members)

    def get_table_queryset(self):
        via_prefetch = Prefetch('roles', queryset=self.children, to_attr='via')
        return self.object.all_members().prefetch_related(via_prefetch)

    @property
    def view_all_members(self):
        return self.search_form.is_valid() and self.search_form.cleaned_data.get('all_members')

    def form_valid(self, form):
        action = form.cleaned_data['action']
        if not self.can_manage_members:
            messages.warning(self.request, _('You are not authorized'))
        elif 'user' in form.cleaned_data:
            if action == 'add':
                self.add_user(form.cleaned_data['user'])
            elif action == 'remove':
                self.remove_user(form.cleaned_data['user'])
        elif 'role' in form.cleaned_data:
            if action == 'add':
                self.add_role(form.cleaned_data['role'])
            elif action == 'remove':
                self.remove_role(form.cleaned_data['role'])
        return super().form_valid(form)

    def add_user(self, user):
        if self.object.members.filter(pk=user.pk).exists():
            messages.warning(self.request, _('User already in this role.'))
        else:
            self.object.members.add(user)
            hooks.call_hooks(
                'event', name='manager-add-role-member', user=self.request.user, role=self.object, member=user
            )
            self.request.journal.record('manager.role.membership.grant', role=self.object, member=user)

    def remove_user(self, user):
        if not self.object.members.filter(pk=user.pk).exists():
            messages.warning(self.request, _('User was not in this role.'))
        else:
            self.object.members.remove(user)
            hooks.call_hooks(
                'event',
                name='manager-remove-role-member',
                user=self.request.user,
                role=self.object,
                member=user,
            )
            self.request.journal.record('manager.role.membership.removal', role=self.object, member=user)

    def add_role(self, role):
        self.object.add_child(role)
        hooks.call_hooks(
            'event', name='manager-add-child-role', user=self.request.user, parent=self.object, child=role
        )
        self.request.journal.record('manager.role.inheritance.addition', parent=self.object, child=role)

    def remove_role(self, role):
        self.object.remove_child(role)
        hooks.call_hooks(
            'event', name='manager-remove-child-role', user=self.request.user, parent=self.object, child=role
        )
        self.request.journal.record('manager.role.inheritance.removal', parent=self.object, child=role)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['role'] = self.object
        return kwargs

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        # if the role has no children, there is no use for the view mixing role
        # and user members, directly use the all members views showing only
        # users with their details
        if not self.children:
            kwargs['data']['search-all_members'] = 'on'
            kwargs['disable_all_members'] = True
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['parents'] = list(
            views.filter_view(
                self.request,
                self.object.parents(include_self=False, annotate=True).order_by(
                    F('ou').asc(nulls_first=True), 'name'
                ),
            )[:11]
        )
        ctx['has_multiple_ou'] = OrganizationalUnit.objects.count() > 1
        ctx['admin_roles'] = views.filter_view(
            self.request, self.object.get_admin_role().children(include_self=False, annotate=True)
        )
        ctx['from_ldap'] = not self.object.can_manage_members
        return ctx

    def is_ou_specified(self):
        return self.search_form.is_valid() and self.search_form.cleaned_data.get('ou')

    def get_table(self, **kwargs):
        show_username = has_show_username()
        if not show_username and self.is_ou_specified():
            show_username = self.is_ou_specified().show_username
        if not show_username:
            exclude = kwargs.setdefault('exclude', [])
            if 'username' not in exclude:
                exclude.append('username')
        return super().get_table(**kwargs)


members = RoleMembersView.as_view()


class RoleDeleteView(RoleViewMixin, views.BaseDeleteView):
    title = _('Delete role')
    template_name = 'authentic2/manager/role_delete.html'

    def post(self, request, *args, **kwargs):
        if not self.can_delete:
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse('a2-manager-roles')

    def delete(self, request, *args, **kwargs):
        role = self.get_object()

        hooks.call_hooks('event', name='manager-delete-role', user=request.user, role=role)
        self.request.journal.record('manager.role.deletion', role=role)
        return super().delete(request, *args, **kwargs)


delete = RoleDeleteView.as_view()


class RoleMembersExportView(views.ExportMixin, RoleMembersView):
    resource_class = resources.UserResource
    permissions = ['a2_rbac.view_role']

    def get_data(self):
        return self.get_table_data()


members_export = RoleMembersExportView.as_view()


class RoleChildrenView(RoleViewMixin, views.HideOUColumnMixin, views.BaseSubTableView):
    title = _('Add child role')
    form_class = forms.ChooseRoleForm
    table_class = tables.InheritanceRolesTable
    search_form_class = forms.RoleSearchForm
    template_name = 'authentic2/manager/roles_inheritance.html'
    permissions = ['a2_rbac.manage_members_role']
    success_url = '.'
    slug_field = 'uuid'

    def get_table_queryset(self):
        qs = super().get_table_queryset()
        qs = qs.exclude(pk=self.object.pk)
        children = self.object.children(annotate=True, include_self=False)
        children = children.annotate(is_direct=Cast('direct', output_field=BooleanField()))
        qs = qs.annotate(
            checked=ExpressionWrapper(Q(pk__in=children.filter(is_direct=True)), output_field=BooleanField())
        )
        qs = qs.annotate(
            indeterminate=ExpressionWrapper(
                Q(pk__in=children.filter(is_direct=False)), output_field=BooleanField()
            )
        )
        rp_qs = RoleParenting.alive.filter(parent__in=children).annotate(name=F('parent__name'))
        qs = qs.prefetch_related(Prefetch('parent_relation', queryset=rp_qs, to_attr='via'))
        return qs

    def form_valid(self, form):
        role = form.cleaned_data['role']
        action = form.cleaned_data['action']
        if action == 'add':
            self.object.add_child(role)
            hooks.call_hooks(
                'event', name='manager-add-child-role', user=self.request.user, parent=self.object, child=role
            )
            self.request.journal.record('manager.role.inheritance.addition', parent=self.object, child=role)
        elif action == 'remove':
            self.object.remove_child(role)
            hooks.call_hooks(
                'event',
                name='manager-remove-child-role',
                user=self.request.user,
                parent=self.object,
                child=role,
            )
            self.request.journal.record('manager.role.inheritance.removal', parent=self.object, child=role)
        return super().form_valid(form)

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['queryset'] = self.request.user.filter_by_perm('a2_rbac.view_role', Role.objects.all())
        return kwargs


children = RoleChildrenView.as_view()


class RoleParentsView(RoleViewMixin, views.HideOUColumnMixin, views.BaseSubTableView):
    title = _('Include permissions from roles')
    form_class = forms.RoleParentForm
    table_class = tables.InheritanceRolesTable
    search_form_class = forms.RoleSearchForm
    template_name = 'authentic2/manager/roles_inheritance.html'
    success_url = '.'
    slug_field = 'uuid'

    @property
    def admin_roles(self):
        if not hasattr(self, 'search_form'):
            return False
        return self.search_form.cleaned_data.get('admin_roles', False)

    def dispatch(self, request, *args, **kwargs):
        if self.get_object().is_internal():
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_table_queryset(self):
        qs = super().get_table_queryset()
        qs = self.request.user.filter_by_perm('a2_rbac.manage_members_role', qs)
        qs = qs.exclude(pk=self.object.pk)
        parents = self.object.parents(annotate=True, include_self=False)
        parents = parents.annotate(is_direct=Cast('direct', output_field=BooleanField()))
        qs = qs.annotate(
            checked=ExpressionWrapper(Q(pk__in=parents.filter(is_direct=True)), output_field=BooleanField())
        )
        qs = qs.annotate(
            indeterminate=ExpressionWrapper(
                Q(pk__in=parents.filter(is_direct=False)), output_field=BooleanField()
            )
        )
        rp_qs = RoleParenting.alive.filter(child__in=parents).annotate(name=F('child__name'))
        qs = qs.prefetch_related(Prefetch('child_relation', queryset=rp_qs, to_attr='via'))
        return qs

    def form_valid(self, form):
        role = form.cleaned_data['role']
        action = form.cleaned_data['action']
        if action == 'add':
            self.object.add_parent(role)
            hooks.call_hooks(
                'event', name='manager-add-child-role', user=self.request.user, parent=role, child=self.object
            )
            self.request.journal.record('manager.role.inheritance.addition', parent=role, child=self.object)
        elif action == 'remove':
            self.object.remove_parent(role)
            hooks.call_hooks(
                'event',
                name='manager-remove-child-role',
                user=self.request.user,
                parent=role,
                child=self.object,
            )
            self.request.journal.record('manager.role.inheritance.removal', parent=role, child=self.object)
        return super().form_valid(form)

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['queryset'] = self.request.user.filter_by_perm(
            'a2_rbac.manage_members_role', Role.objects.all()
        )
        return kwargs


parents = RoleParentsView.as_view()


class RoleAddAdminRoleView(
    views.AjaxFormViewMixin,
    views.TitleMixin,
    views.PermissionMixin,
    views.FormNeedsRequest,
    SingleObjectMixin,
    FormView,
):
    title = _('Add admin role')
    model = Role
    form_class = forms.RolesForm
    success_url = '..'
    template_name = 'authentic2/manager/form.html'
    permissions = ['a2_rbac.change_role']

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        administered_role = self.get_object()
        for role in form.cleaned_data['roles']:
            administered_role.get_admin_role().add_child(role)
            hooks.call_hooks(
                'event',
                name='manager-add-admin-role',
                user=self.request.user,
                role=administered_role,
                admin_role=role,
            )
            self.request.journal.record(
                'manager.role.administrator.role.addition', role=administered_role, admin_role=role
            )
        return super().form_valid(form)


add_admin_role = RoleAddAdminRoleView.as_view()


class RoleRemoveAdminRoleView(
    views.TitleMixin, views.AjaxFormViewMixin, SingleObjectMixin, views.PermissionMixin, TemplateView
):
    title = _('Remove admin role')
    model = Role
    success_url = '../..'
    template_name = 'authentic2/manager/role_remove_admin_role.html'
    permissions = ['a2_rbac.change_role']

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.child = self.get_queryset().get(pk=kwargs['role_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['child'] = self.child
        return ctx

    def post(self, request, *args, **kwargs):
        self.object.get_admin_role().remove_child(self.child)
        hooks.call_hooks(
            'event',
            name='manager-remove-admin-role',
            user=self.request.user,
            role=self.object,
            admin_role=self.child,
        )
        self.request.journal.record(
            'manager.role.administrator.role.removal', role=self.object, admin_role=self.child
        )
        return redirect(self.request, self.success_url)


remove_admin_role = RoleRemoveAdminRoleView.as_view()


class RoleAddAdminUserView(
    views.AjaxFormViewMixin,
    views.TitleMixin,
    views.PermissionMixin,
    views.FormNeedsRequest,
    SingleObjectMixin,
    FormView,
):
    title = _('Add admin user')
    model = Role
    form_class = forms.UsersForm
    success_url = '..'
    template_name = 'authentic2/manager/form.html'
    permissions = ['a2_rbac.change_role']

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        administered_role = self.get_object()
        for user in form.cleaned_data['users']:
            administered_role.get_admin_role().members.add(user)
            hooks.call_hooks(
                'event',
                name='manager-add-admin-role-user',
                user=self.request.user,
                role=administered_role,
                admin=user,
            )
            self.request.journal.record(
                'manager.role.administrator.user.addition', role=administered_role, admin_user=user
            )
        return super().form_valid(form)


add_admin_user = RoleAddAdminUserView.as_view()


class RoleRemoveAdminUserView(
    views.TitleMixin, views.AjaxFormViewMixin, SingleObjectMixin, views.PermissionMixin, TemplateView
):
    title = _('Remove admin user')
    model = Role
    success_url = '../..'
    template_name = 'authentic2/manager/role_remove_admin_user.html'
    permissions = ['a2_rbac.change_role']

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.user = get_user_model().objects.get(pk=kwargs['user_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['user'] = self.user
        return ctx

    def post(self, request, *args, **kwargs):
        self.object.get_admin_role().members.remove(self.user)
        hooks.call_hooks(
            'event',
            name='remove-remove-admin-role-user',
            user=self.request.user,
            role=self.object,
            admin=self.user,
        )
        self.request.journal.record(
            'manager.role.administrator.user.removal', role=self.object, admin_user=self.user
        )
        return redirect(self.request, self.success_url)


remove_admin_user = RoleRemoveAdminUserView.as_view()


class RolesImportView(
    views.PermissionMixin, views.TitleMixin, views.MediaMixin, views.FormNeedsRequest, FormView
):
    form_class = forms.RolesImportForm
    model = Role
    template_name = 'authentic2/manager/import_form.html'
    title = _('Roles Import')

    def get_initial(self):
        initial = super().get_initial()
        search_ou = self.request.GET.get('search-ou')
        if search_ou:
            initial['ou'] = search_ou
        return initial

    def post(self, request, *args, **kwargs):
        if not self.can_add:
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        self.ou = form.cleaned_data['ou']
        try:
            context = data_transfer.ImportContext(
                import_ous=False, set_ou=self.ou, allowed_ous=set(form.fields['ou'].queryset)
            )
            with transaction.atomic():
                data_transfer.import_site(form.cleaned_data['site_json'], context)
        except ValidationError as e:
            form.add_error('site_json', e)
            return self.form_invalid(form)

        return super().form_valid(form)

    def get_success_url(self):
        if self.ou:
            message = _('Roles have been successfully imported inside "%s" organizational unit.') % self.ou
            querystring = '?search-ou=%s' % self.ou.pk
        else:
            message = _('Roles have been successfully imported.')
            querystring = ''

        messages.success(self.request, message)
        return reverse('a2-manager-roles') + querystring


roles_import = RolesImportView.as_view()


class RolesCsvImportView(
    views.PermissionMixin, views.TitleMixin, views.MediaMixin, views.FormNeedsRequest, FormView
):
    form_class = forms.RolesCsvImportForm
    model = Role
    template_name = 'authentic2/manager/roles_csv_import_form.html'
    title = _('Roles CSV Import')

    def get_initial(self):
        initial = super().get_initial()
        search_ou = self.request.GET.get('search-ou')
        if search_ou:
            initial['ou'] = search_ou
        return initial

    def post(self, request, *args, **kwargs):
        if not self.can_add:
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        self.ou = form.cleaned_data['ou']
        for role in form.roles:
            role.save()
        return super().form_valid(form)

    def get_success_url(self):
        messages.success(
            self.request,
            _('Roles have been successfully imported inside "%s" organizational unit.') % self.ou,
        )
        return reverse('a2-manager-roles') + '?search-ou=%s' % self.ou.pk


roles_csv_import = RolesCsvImportView.as_view()


class RolesCsvImportSampleView(TemplateView):
    template_name = 'authentic2/manager/sample_roles.txt'
    content_type = 'text/csv'


roles_csv_import_sample = RolesCsvImportSampleView.as_view()


class RoleJournal(views.PermissionMixin, JournalViewWithContext, BaseJournalView):
    template_name = 'authentic2/manager/role_journal.html'
    permissions = ['a2_rbac.view_role']
    title = _('Journal')

    @cached_property
    def context(self):
        return get_object_or_404(Role, pk=self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object'] = self.context
        ctx['object_name'] = str(self.context)
        return ctx


journal = RoleJournal.as_view()


class RolesJournal(views.SearchOUMixin, views.PermissionMixin, JournalViewWithContext, BaseJournalView):
    template_name = 'authentic2/manager/roles_journal.html'
    permissions = ['a2_rbac.view_role']
    title = _('Journal')

    @cached_property
    def context(self):
        return Role


roles_journal = RolesJournal.as_view()


class UserOrRoleSelect2View(DetailView):
    form_class = forms.ChooseUserOrRoleForm
    model = Role

    def get(self, request, *args, **kwargs):
        if not self.request.user.is_authenticated or not hasattr(self.request.user, 'filter_by_perm'):
            raise Http404('Invalid user')

        role = self.get_object()

        field_id = self.kwargs.get('field_id', self.request.GET.get('field_id', None))
        if not field_id:
            raise BadRequest('Invalid ID')
        try:
            crypto.loads(field_id)
        except (crypto.SignatureExpired, crypto.BadSignature):
            raise Http404('Invalid or expired signature.')

        search_term = request.GET.get('term', '')
        try:
            page_number = int(request.GET.get('page', 1))
        except ValueError:
            page_number = 1

        role_qs = self.form_class.get_role_queryset(self.request.user, role)
        children = role.children(annotate=True)
        children = children.annotate(is_direct=Cast('direct', output_field=BooleanField()))
        role_qs = role_qs.exclude(pk__in=children.filter(is_direct=True))
        role_qs = self.filter_queryset(role_qs, search_term, ['name', 'service__name', 'ou__name'])

        role_paginator = Paginator(role_qs, 10)
        try:
            role_page = role_paginator.page(page_number)
        except EmptyPage:
            role_page = []
            has_next = False
        else:
            has_next = role_page.has_next()

        user_page = []
        if not has_next:
            user_qs = self.form_class.get_user_queryset(self.request.user, role)
            user_qs = user_qs.exclude(roles=role)
            user_qs = self.filter_queryset(
                user_qs, search_term, ['username', 'first_name', 'last_name', 'email']
            )

            page_number = page_number - role_paginator.num_pages + 1
            user_paginator = Paginator(user_qs, 10)
            try:
                user_page = user_paginator.page(page_number)
            except EmptyPage:
                has_next = False
            else:
                has_next = user_page.has_next()

        return JsonResponse(
            {
                'results': [self.get_choice(obj) for obj in list(role_page) + list(user_page)],
                'more': has_next,
            }
        )

    @staticmethod
    def filter_queryset(qs, search_term, search_fields):
        lookups = Q()
        for term in [term for term in search_term.split() if not term == '']:
            lookups &= reduce(Q.__or__, (Q(**{'%s__icontains' % field: term}) for field in search_fields))
        return qs.filter(lookups)

    def get_choice(self, obj):
        if isinstance(obj, Role):
            text = label_from_role(obj)
            key = 'role-%s'
        elif isinstance(obj, User):
            text = label_from_user(obj)
            key = 'user-%s'
        return {'id': key % obj.pk, 'text': text}


user_or_role_select2 = UserOrRoleSelect2View.as_view()


class RoleSummaryView(RoleViewMixin, views.MediaMixin, DetailView):
    template_name = 'authentic2/manager/role_summary.html'
    permissions = ['a2_rbac.view_role']

    @cached_property
    def context(self):
        return get_object_or_404(Role, pk=self.kwargs['pk'])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['object'] = self.context
        ctx['parents'] = self.object.parents(include_self=False, annotate=True).order_by(
            F('ou').asc(nulls_first=True), 'name'
        )
        ctx['admin_roles'] = list(self.object.get_admin_role().children(include_self=False))
        roles_summary_cache = get_roles_summary_cache()
        summary_data = roles_summary_cache.get(self.context.uuid, {})
        ctx['summary_data'] = summary_data or {'type_objects': [], 'parents_type_objects': []}
        ctx['summary_data_error'] = roles_summary_cache.get('error')
        return ctx


summary = RoleSummaryView.as_view()
