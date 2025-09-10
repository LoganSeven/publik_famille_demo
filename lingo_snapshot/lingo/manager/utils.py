# lingo - payment and billing system
# Copyright (C) 2024  Entr'ouvert
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

from django.core.exceptions import PermissionDenied


class StaffRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class CanBeManagedCheckMixin:
    def check_object(self, obj):
        if not obj.can_be_managed(self.request.user):
            raise PermissionDenied()


class CanBeManagedRequiredMixin(CanBeManagedCheckMixin):
    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        self.check_object(obj)
        return obj


class CanBeInvoicedCheckMixin:
    def check_object(self, obj):
        if not obj.can_be_invoiced(self.request.user):
            raise PermissionDenied()


class CanBeControlledCheckMixin:
    def check_object(self, obj):
        if not obj.can_be_controlled(self.request.user):
            raise PermissionDenied()


class CanBeViewedCheckMixin:
    def check_object(self, obj):
        if not obj.can_be_viewed(self.request.user):
            raise PermissionDenied()


class CanBeViewedRequiredMixin(CanBeViewedCheckMixin):
    def get_object(self, queryset=None):
        obj = super().get_object(queryset=queryset)
        self.check_object(obj)
        return obj
