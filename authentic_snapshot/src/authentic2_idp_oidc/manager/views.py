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

from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache

from authentic2.manager import views
from authentic2_idp_oidc import app_settings
from authentic2_idp_oidc.models import OIDCClaim, OIDCClient

from . import forms


class OIDCServiceAddView(views.ActionMixin, views.BaseAddView):
    form_class = forms.OIDCClientAddForm
    model = OIDCClient
    title = _('Add OIDC service')
    permissions = ['authentic2.add_service']
    action = _('Add')

    def get_success_url(self):
        # add default claims mappings after creating service
        for mapping in app_settings.DEFAULT_MAPPINGS:
            OIDCClaim.objects.get_or_create(client=self.object, **mapping)
        return reverse('a2-manager-service', kwargs={'service_pk': self.object.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.journal.record('manager.service.creation', service=form.instance)
        return response


add_oidc_service = OIDCServiceAddView.as_view()


class MixinClaimSuccessUrl:
    def get_success_url(self):
        return (
            reverse('a2-manager-service-settings', kwargs={'service_pk': self.object.client.pk})
            + '#oidc-claims'
        )


class OIDCClaimAddView(MixinClaimSuccessUrl, views.ActionMixin, views.BaseAddView):
    form_class = forms.OIDCClaimForm
    model = OIDCClaim
    title = _('Add OIDC Claim')
    action = _('Add')
    permissions = ['authentic2.admin_service']
    permission_model = OIDCClient
    permission_pk_url_kwargs = 'service_pk'

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.client = OIDCClient.objects.get(pk=self.kwargs['service_pk'])
        obj.save()
        response = super().form_valid(form)
        self.request.journal.record(
            'manager.service.edit',
            service=form.instance.client,
            new_value={
                'name': form.cleaned_data['name'],
                'value': form.cleaned_data['value'],
                'scopes': form.cleaned_data['scopes'],
            },
            conf_name='OIDC claim',
        )
        return response


oidc_claim_add = OIDCClaimAddView.as_view()


class BaseClaimView(MixinClaimSuccessUrl):
    model = OIDCClaim
    pk_url_kwarg = 'claim_pk'

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(client__pk=self.kwargs['service_pk'])


class OIDCClaimEditView(BaseClaimView, views.BaseEditView):
    title = _('Edit OpenID Claim')
    form_class = forms.OIDCClaimForm
    permissions = ['authentic2.admin_service']
    permission_model = OIDCClient
    permission_pk_url_kwarg = 'service_pk'

    def form_valid(self, form):
        response = super().form_valid(form)
        changed = form.changed_data
        if not changed:
            return response
        old = {k: form.initial[k] for k in changed}
        new = {k: form.cleaned_data[k] for k in changed}
        self.request.journal.record(
            'manager.service.edit',
            service=form.instance.client,
            new_value=new,
            old_value=old,
            conf_name='OIDC claim',
        )
        return response


oidc_claim_edit = OIDCClaimEditView.as_view()


class OIDCClaimDeleteView(BaseClaimView, views.BaseDeleteView):
    title = _('Delete OpenID Claim')
    permissions = ['authentic2.admin_service']
    permission_model = OIDCClient
    permission_pk_url_kwarg = 'service_pk'

    def form_valid(self, form):
        return self.log_delete(super().delete, form)

    def log_delete(self, callback, *args, **kwargs):
        claim = self.get_object()
        service = claim.client
        old = {'name': claim.name, 'value': claim.value, 'scopes': claim.scopes}
        self.request.journal.record(
            'manager.service.edit',
            service=service,
            old_value=old,
            conf_name='OIDC claim',
        )
        return callback(*args, **kwargs)


oidc_claim_delete = OIDCClaimDeleteView.as_view()


@never_cache
def ServicesGenerateUUIDView(request):
    from authentic2_idp_oidc.models import generate_uuid

    return JsonResponse({'uuid': generate_uuid()})
