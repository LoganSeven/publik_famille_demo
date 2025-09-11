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

from django.urls import reverse
from django.views.generic import FormView

from .forms import AddLibertyProviderFromUrlForm


class AdminAddFormViewMixin:
    model_admin = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            {
                'app_label': self.model_admin.model._meta.app_label,
                'has_change_permission': self.model_admin.has_change_permission(self.request),
                'opts': self.model_admin.model._meta,
            }
        )
        return ctx


class AddLibertyProviderFromUrlView(AdminAddFormViewMixin, FormView):
    form_class = AddLibertyProviderFromUrlForm
    template_name = 'admin/saml/libertyprovider/add_from_url.html'

    def get_form_kwargs(self, **kwargs):
        kwargs = super().get_form_kwargs(**kwargs)
        if 'entity_id' in self.request.GET:
            initial = kwargs.setdefault('initial', {})
            initial['url'] = self.request.GET['entity_id']
        return kwargs

    def form_valid(self, form):
        form.save()
        self.success_url = reverse('admin:saml_libertyprovider_change', args=(form.instance.id,))
        return super().form_valid(form)
