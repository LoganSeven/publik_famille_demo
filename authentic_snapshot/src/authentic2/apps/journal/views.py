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

from django.views.generic import TemplateView
from django.views.generic.edit import FormMixin

from . import forms, models


class JournalView(FormMixin, TemplateView):
    form_class = forms.JournalForm
    limit = 20

    def get_events(self):
        return models.Event.objects.all()

    def get_form_kwargs(self):
        queryset = self.get_events()
        return {
            'data': self.request.GET,
            'limit': self.limit,
            'queryset': queryset,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx['form']
        ctx['page'] = form.page
        ctx['date_hierarchy'] = form.date_hierarchy
        return ctx


class ContextDecoratedEvent:
    def __init__(self, context, event):
        self.context = context
        self.event = event

    def __getattr__(self, name):
        return getattr(self.event, name)

    @property
    def message(self):
        return self.event.message_in_context(self.context)


class ContextDecoratedPage:
    def __init__(self, context, page):
        self.context = context
        self.page = page

    def __getattr__(self, name):
        return getattr(self.page, name)

    def __iter__(self):
        return (ContextDecoratedEvent(self.context, event) for event in self.page)


class JournalViewWithContext(JournalView):
    context = None

    def get_events(self):
        qs = super().get_events()
        qs = qs.which_references(self.context)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['context'] = self.context
        ctx['page'] = ContextDecoratedPage(self.context, ctx['page'])
        return ctx
