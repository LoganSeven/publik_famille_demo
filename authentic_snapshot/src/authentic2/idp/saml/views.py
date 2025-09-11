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
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, View

from authentic2.saml.models import LibertyFederation


class FederationCreateView(View):
    pass


class FederationDeleteView(DeleteView):
    model = LibertyFederation

    def get_queryset(self):
        # check current user owns this federation
        qs = super().get_queryset()
        return qs.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.user = None
        self.object.save()
        messages.info(request, _('Federation to {0} deleted').format(self.object.sp.liberty_provider.name))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return self.request.POST.get(REDIRECT_FIELD_NAME, reverse('auth_homepage'))


delete_federation = FederationDeleteView.as_view()
create_federation = FederationCreateView.as_view()
