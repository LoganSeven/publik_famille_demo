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

import uuid

from django.db.models import F
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.manager import forms
from authentic2.manager.views import MediaMixin, PermissionMixin, TitleMixin
from authentic2.models import APIClient

from . import views


class APIClientsMixin(PermissionMixin, MediaMixin, TitleMixin):
    model = APIClient
    permissions = ['authentic2.admin_apiclient']
    permissions_global = False

    def get_queryset(self):
        if not self.request.user:
            return self.model.objects.none()

        qs = self.model.objects.all()
        if self.request.user.has_perm('authentic2.admin_apiclient'):
            return qs

        allowed_ous = []
        for ou in OrganizationalUnit.objects.all():
            if self.request.user.has_ou_perm('authentic2.admin_apiclient', ou):
                allowed_ous.append(ou)
        return qs.filter(ou__in=allowed_ous)


class APIClientsFormViewMixin(APIClientsMixin):
    def get_form(self, form_class=None):
        form = super().get_form(form_class=form_class)
        if not self.request.user.has_perm('authentic2.admin_apiclient'):
            allowed_ous = []
            for ou in OrganizationalUnit.objects.all():
                if self.request.user.has_ou_perm('authentic2.admin_apiclient', ou):
                    allowed_ous.append(ou.id)
            form.fields['ou'].queryset = OrganizationalUnit.objects.filter(id__in=allowed_ous)
            form.fields['ou'].required = True
            form.fields['ou'].empty_label = None
        api_client = self.object
        if api_client and api_client.ou is not None:
            form.fields['apiclient_roles'].queryset = Role.objects.filter(ou=api_client.ou).exclude(
                slug__startswith='_'
            )
        return form


class APIClientsView(APIClientsMixin, ListView):
    template_name = 'authentic2/manager/api_clients.html'
    title = _('API Clients')


listing = APIClientsView.as_view()


class APIClientDetailView(APIClientsMixin, DetailView):
    template_name = 'authentic2/manager/api_client_detail.html'

    @property
    def title(self):
        return str(self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['parent_roles'] = list(
            self.object.apiclient_roles.parents(include_self=False, annotate=False)
            .exclude(id__in=self.object.apiclient_roles.values_list('id'))
            .order_by(F('ou').asc(nulls_first=True), 'name')
        )

        roles = (
            self.object.apiclient_roles.parents(include_self=True, annotate=False)
            .select_related('ou')
            .order_by(F('ou').asc(nulls_first=True), 'name')
        )

        context['roles_with_access'] = views.filter_view(self.request, roles)
        ou_list = roles.distinct('ou').values_list('ou__pk', flat=True)
        context['display_ou'] = len(ou_list) > 1 or getattr(self.object.ou, 'pk', None) not in ou_list

        context['api_client'] = self.object

        # IP restriction feature flag
        context['A2_API_USERS_ALLOW_IP_RESTRICTIONS'] = a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS
        return context


detail = APIClientDetailView.as_view()


class APIClientAddView(APIClientsFormViewMixin, CreateView):
    template_name = 'authentic2/manager/api_client_form.html'
    title = _('New API client')
    form_class = forms.APIClientForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = reverse('a2-manager-api-clients')
        return context

    def get_success_url(self):
        return reverse('a2-manager-api-client-detail', kwargs={'pk': self.object.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['initial'] = {'password': str(uuid.uuid4())}
        return kwargs


add = APIClientAddView.as_view()


class APIClientEditView(APIClientsFormViewMixin, UpdateView):
    template_name = 'authentic2/manager/api_client_form.html'
    title = _('Edit API client')
    form_class = forms.APIClientEditForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cancel_url'] = reverse('a2-manager-api-client-detail', kwargs={'pk': self.object.pk})
        return context


edit = APIClientEditView.as_view()


class APIClientDeleteView(APIClientsMixin, DeleteView):
    template_name = 'authentic2/manager/api_client_delete.html'
    title = _('Delete API client')
    success_url = reverse_lazy('a2-manager-api-clients')


delete = APIClientDeleteView.as_view()
