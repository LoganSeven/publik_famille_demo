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

from django.contrib import messages
from django.utils.translation import gettext as _

from authentic2.models import Service, Setting

from . import forms, role_views, tables, views


class ServicesView(views.HideOUColumnMixin, views.BaseTableView):
    model = Service
    template_name = 'authentic2/manager/services.html'
    table_class = tables.ServiceTable
    search_form_class = forms.ServiceSearchForm
    permissions = ['authentic2.search_service']
    title = _('Services')

    def get_search_form_kwargs(self):
        kwargs = super().get_search_form_kwargs()
        kwargs['queryset'] = self.get_queryset()
        return kwargs


listing = ServicesView.as_view()


class ServiceMixin:
    def get_object(self, queryset=None):
        service = super().get_object(queryset)
        if hasattr(service, 'oidcclient'):
            return service.oidcclient
        return service


class ServiceView(
    ServiceMixin,
    views.SimpleSubTableView,
    role_views.RoleViewMixin,
    views.MediaMixin,
    views.FormNeedsRequest,
    views.FormView,
):
    search_form_class = forms.NameSearchForm
    model = Service
    pk_url_kwarg = 'service_pk'
    template_name = 'authentic2/manager/service.html'
    table_class = tables.ServiceRolesTable
    permissions = ['authentic2.view_service']
    form_class = forms.ChooseRoleForm
    success_url = '.'

    @property
    def title(self):
        return str(self.object)

    def get_table_queryset(self):
        return self.object.authorized_roles.all()

    def get(self, request, *args, **kwargs):
        result = super().get(request, *args, **kwargs)
        self.service = self.object
        return result

    def form_valid(self, form):
        service = self.get_object()
        role = form.cleaned_data['role']
        action = form.cleaned_data['action']
        if self.can_change:
            if action == 'add':
                if self.object.authorized_roles.filter(pk=role.pk).exists():
                    messages.warning(self.request, _('Role already authorized in this service.'))
                else:
                    self.object.add_authorized_role(role)
                    self.request.journal.record(
                        'manager.service.role.add',
                        service=service,
                        role=role,
                    )

            elif action == 'remove':
                self.object.remove_authorized_role(role)
                self.request.journal.record('manager.service.role.delete', service=service, role=role)
        else:
            messages.warning(self.request, _('You are not authorized'))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        kwargs['form'] = self.get_form()
        ctx = super().get_context_data(**kwargs)
        ctx['roles_table'] = tables.RoleTable(self.object.roles.all())
        return ctx


service_detail = ServiceView.as_view()


class ServiceSettingsView(
    ServiceMixin,
    views.BaseDetailView,
):
    model = Service
    pk_url_kwarg = 'service_pk'
    template_name = 'authentic2/manager/service_settings.html'
    permissions = ['authentic2.change_service']

    def get_context_data(self, **kwargs):
        self.service = self.object
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.object.get_manager_context_data())
        return ctx


service_settings = ServiceSettingsView.as_view()


class ServiceEditView(ServiceMixin, views.BaseEditView):
    model = Service
    pk_url_kwarg = 'service_pk'
    template_name = 'authentic2/manager/service_edit.html'
    title = _('Edit service')
    permissions = ['authentic2.change_service']
    success_url = '..'

    def get_form_class(self):
        if self.object.manager_form_class:
            return self.object.manager_form_class
        return super().get_form_class()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        for fieldname in form.changed_data:
            old = form.initial[fieldname]
            new = form.cleaned_data[fieldname]
            if fieldname in ('client_secret', 'client_id'):
                new = 'xxxNEW_SECRETxxx'
            self.request.journal.record(
                'manager.service.edit',
                service=form.instance,
                old_value=old,
                new_value=new,
                conf_name=fieldname,
            )
        return response


edit_service = ServiceEditView.as_view()


class ServiceDeleteView(views.BaseDeleteView):
    model = Service
    pk_url_kwarg = 'service_pk'
    permissions = ['authentic2.delete_service']
    title = _('Delete service')

    def form_valid(self, form):
        return self.log_delete(super().form_valid, form)

    def log_delete(self, callback, *args, **kwargs):
        service = self.get_object()
        self.request.journal.record('manager.service.deletion', service=service)
        return callback(*args, **kwargs)


delete_service = ServiceDeleteView.as_view()


class ServicesSettingsView(views.FormView):
    template_name = 'authentic2/manager/services_settings.html'
    form_class = forms.ServicesSettingsForm
    title = _('Edit services-related settings')
    success_url = '..'
    permissions = ['authentic2.change_service']

    def form_valid(self, form):
        for key, value in form.cleaned_data.items():
            try:
                setting = Setting.objects.get(key=key)
            except Setting.DoesNotExist:
                continue
            setting.value = value
            setting.save()
        return super().form_valid(form)


services_settings = ServicesSettingsView.as_view()
