# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
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

from lingo.export_import.models import Application


class WithApplicationsMixin:
    def with_applications_dispatch(self, request):
        self.application = None
        self.no_application = False
        if 'application' in self.request.GET:
            self.application = get_object_or_404(
                Application, slug=self.request.GET['application'], visible=True
            )
        elif 'no-application' in self.request.GET:
            self.no_application = True

    def with_applications_context_data(self, context):
        if self.application:
            context['application'] = self.application
        elif not self.no_application:
            Application.populate_objects(self.model, self.object_list)
            context['applications'] = Application.select_for_object_class(self.model)
        context['no_application'] = self.no_application
        return context

    def with_applications_queryset(self):
        if self.application:
            return self.application.get_objects_for_object_class(self.model)
        if self.no_application:
            return Application.get_orphan_objects_for_object_class(self.model)
        return super().get_queryset()
