# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

from django.shortcuts import get_object_or_404
from django.urls import reverse

from .models import Agenda


class AgendaMixin:
    agenda = None
    tab_anchor = None

    def set_agenda(self, **kwargs):
        self.agenda = get_object_or_404(Agenda, id=kwargs.get('pk'))

    def dispatch(self, request, *args, **kwargs):
        self.set_agenda(**kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['agenda'] = self.agenda
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        kwargs['instance'].agenda = self.agenda
        return kwargs

    def get_success_url(self):
        url = reverse('lingo-manager-agenda-detail', kwargs={'pk': self.agenda.pk})
        if self.tab_anchor:
            url += '#open:%s' % self.tab_anchor
        return url
