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

import base64
import functools
import operator
import pickle

from django.contrib.auth import get_user_model
from django.utils.encoding import force_str
from django_select2.forms import ModelSelect2MultipleWidget, ModelSelect2Widget

from authentic2.a2_rbac.models import Role
from authentic2.utils import crypto
from authentic2_idp_oidc.models import OIDCAuthorization

from . import utils


class SplitTermMixin:
    split_term_operator = operator.__or__

    def filter_queryset(self, request, term, queryset=None, **dependent_fields):
        if isinstance(request, str):
            # ModelSelect2Mixin.filter_queryset prototype changes in django_select2>=7 versions.
            # _legacy_ filter_queryset will be dropped along lower django_select2<7 versions support.
            return self._legacy_filter_queryset(request, term)

        if queryset is not None:
            qs = queryset
        else:
            qs = self.get_queryset()
        if not term.strip():
            return qs.all()
        queries = []
        for term in term.split():
            queries.append(super().filter_queryset(request, term, queryset=qs, **dependent_fields))
        qs = functools.reduce(self.split_term_operator, queries)
        return qs

    def _legacy_filter_queryset(self, term, queryset=None):
        if queryset is not None:
            qs = queryset
        else:
            qs = self.get_queryset()
        if not term.strip():
            return qs.all()
        queries = []
        for term in term.split():
            queries.append(super().filter_queryset(term, queryset=qs))
        qs = functools.reduce(self.split_term_operator, queries)
        return qs


class Select2Mixin:
    class Media:
        js = ('authentic2/manager/js/select2_locale.js',)

    def build_attrs(self, *args, **kwargs):
        attrs = super().build_attrs(*args, **kwargs)
        field_data = {
            'class': self.__class__.__name__,
            'where_clause': force_str(base64.b64encode(pickle.dumps(self.queryset.query.where))),
        }
        attrs['data-field_id'] = crypto.dumps(field_data)
        return attrs

    @classmethod
    def get_initial_queryset(cls):
        return cls.model.objects.all()


class SimpleModelSelect2Widget(Select2Mixin, ModelSelect2Widget):
    def __init__(self, *args, **kwargs):
        # upstream django_select2 uses django's url-routing namespaces, not supported here yet.
        # here we replace the default 'django_select2:auto-json' with our current view name.
        kwargs['data_view'] = 'django_select2-json'
        super().__init__(*args, **kwargs)


class SimpleModelSelect2MultipleWidget(Select2Mixin, ModelSelect2MultipleWidget):
    def __init__(self, *args, **kwargs):
        # upstream django_select2 uses django's url-routing namespaces, not supported here yet.
        # here we replace the default 'django_select2:auto-json' with our current view name.
        kwargs['data_view'] = 'django_select2-json'
        super().__init__(*args, **kwargs)


class SearchUserWidgetMixin(SplitTermMixin):
    model = get_user_model()
    search_fields = [
        'username__icontains',
        'first_name__icontains',
        'last_name__icontains',
        'email__icontains',
    ]

    def label_from_instance(self, user):
        return utils.label_from_user(user)


class ChooseUserWidget(SearchUserWidgetMixin, SimpleModelSelect2Widget):
    pass


class ChooseUsersWidget(SearchUserWidgetMixin, SimpleModelSelect2MultipleWidget):
    pass


class SearchRoleWidgetMixin(SplitTermMixin):
    model = Role
    split_term_operator = operator.__and__
    search_fields = [
        'name__icontains',
        'service__name__icontains',
        'ou__name__icontains',
    ]

    def label_from_instance(self, obj):
        return utils.label_from_role(obj)


class ChooseRoleWidget(SearchRoleWidgetMixin, SimpleModelSelect2Widget):
    @classmethod
    def get_initial_queryset(cls):
        return cls.model.objects.exclude(slug__startswith='_')


class ChooseRolesWidget(SearchRoleWidgetMixin, SimpleModelSelect2MultipleWidget):
    @classmethod
    def get_initial_queryset(cls):
        return cls.model.objects.exclude(slug__startswith='_')


class ChooseManageableMemberRolesWidget(SearchRoleWidgetMixin, SimpleModelSelect2MultipleWidget):
    perm = 'manage_members'


class ChooseManageableMemberRoleWidget(SearchRoleWidgetMixin, SimpleModelSelect2Widget):
    perm = 'manage_members'


class ChooseUserAuthorizationsWidget(SimpleModelSelect2Widget):
    model = OIDCAuthorization
