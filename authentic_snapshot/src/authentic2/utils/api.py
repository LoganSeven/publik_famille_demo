# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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


from django.db import models
from rest_framework import exceptions, permissions, serializers

from . import converters


class NaturalKeyRelatedField(serializers.RelatedField):
    def to_representation(self, value):
        if value is None:
            return None
        return self._instance_to_natural_key(value)

    def to_internal_value(self, data):
        if data is None:
            return None
        if not isinstance(data, dict):
            raise exceptions.ValidationError('natural key must be a dictionary')
        return self._natural_key_to_instance(self.get_queryset(), data)

    def _instance_to_natural_key(self, instance):
        model = type(instance)
        fields = set()
        for natural_key_description in model._meta.natural_key:
            for name in natural_key_description:
                name = name.split('__')[0]
                fields.add(name)
        raw = {name: getattr(instance, name) for name in fields}
        return {
            name: self._instance_to_natural_key(value) if isinstance(value, models.Model) else value
            for name, value in raw.items()
        }

    def _natural_key_to_instance(self, queryset, data):
        if data is None:
            return data

        model = queryset.model
        natural_keys = {}
        for name, value in data.items():
            field = model._meta.get_field(name)
            if field.related_model:
                qs = field.related_model._base_manager
                natural_keys[name] = self._natural_key_to_instance(qs, value)
            else:
                if not isinstance(value, (int, str, bool)):
                    raise exceptions.ValidationError('natural key\' scalar must be a int, str or bool')
                natural_keys[name] = value
        for natural_key_description in model._meta.natural_key:
            lookups = {}
            for name in natural_key_description:
                real_name = name.split('__')[0]
                if real_name not in natural_keys:
                    break
                value = natural_keys[real_name]
                if name.endswith('__isnull'):
                    if value is not None:
                        break
                    lookups[name] = True
                else:
                    lookups[name] = value
            else:
                try:
                    return queryset.get(**lookups)
                except model.DoesNotExist:
                    pass
                except model.MultipleObjectsReturned:
                    raise exceptions.ValidationError('multiple objects returned')
        raise exceptions.ValidationError('object not found')


class DjangoRBACPermission(permissions.BasePermission):
    perms_map = {
        'GET': [],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['add'],
        'PUT': ['change'],
        'PATCH': ['change'],
        'DELETE': ['delete'],
    }
    object_perms_map = {
        'GET': ['view'],
    }

    def __init__(self, perms_map=None, object_perms_map=None):
        self.perms_map = perms_map or dict(self.perms_map)
        if object_perms_map:
            self.object_perms_map = object_perms_map
        else:
            self.object_perms_map = dict(self.object_perms_map)
            for k, v in self.perms_map.items():
                if v:
                    self.object_perms_map[k] = v

    def _get_queryset(self, view):
        assert hasattr(view, 'get_queryset') or getattr(view, 'queryset', None) is not None, (
            'Cannot apply {} on a view that does not set ' '`.queryset` or have a `.get_queryset()` method.'
        ).format(self.__class__.__name__)

        if hasattr(view, 'get_queryset'):
            queryset = view.get_queryset()
            assert queryset is not None, f'{view.__class__.__name__}.get_queryset() returned None'
            return queryset
        return view.queryset

    def _get_required_permissions(self, method, model_cls, perms_map):
        """
        Given a model and an HTTP method, return the list of permission
        codes that the user is required to have.
        """
        app_label = model_cls._meta.app_label
        model_name = model_cls._meta.model_name

        if method not in perms_map:
            raise exceptions.MethodNotAllowed(method)

        return [f'{app_label}.{perm}_{model_name}' if '.' not in perm else perm for perm in perms_map[method]]

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        queryset = self._get_queryset(view)
        perms = self._get_required_permissions(request.method, queryset.model, self.perms_map)

        return request.user.has_perms(perms)

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        queryset = self._get_queryset(view)
        perms = self._get_required_permissions(request.method, queryset.model, self.object_perms_map)

        return request.user.has_perms(perms, obj=obj)

    def __call__(self):
        return self

    def __repr__(self):
        return f'<DjangoRBACPermission perms_map={self.perms_map} object_perms_map={self.object_perms_map}>'


def get_boolean_flag(request, name, default=False):
    if not request:
        return default
    return converters.string_to_boolean(request.GET.get(name, ''), default=default)
