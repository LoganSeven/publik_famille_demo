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

from django.contrib.auth import get_user_model
from import_export.fields import Field
from import_export.resources import ModelResource
from import_export.widgets import Widget

from authentic2.a2_rbac.models import Role

User = get_user_model()


class ListWidget(Widget):
    def clean(self, value):
        raise NotImplementedError

    def render(self, value, obj):
        return ', '.join(str(v) for v in value.all())


class EscapeFormulaMixin:
    @staticmethod
    def escape(value):
        '''Escape string value which could be interpreted as a formula in Excel.

        https://owasp.org/www-community/attacks/CSV_Injection
        '''
        if isinstance(value, str) and value.startswith(('=', '@', '+', '-', '\t', '\r')):
            value = "'" + value
        return value

    def export_resource(self, obj):
        row = super().export_resource(obj)
        for i, value in enumerate(row):
            new_value = self.escape(value)
            if new_value != value:
                row[i] = new_value
        return row


class UserResource(EscapeFormulaMixin, ModelResource):
    roles = Field()

    def dehydrate_roles(self, instance):
        roles = {role for role in instance.roles.all()}
        # optimization as parent_relation is prefetched, filter deleted__isnull=True using python
        parents = {rp.parent for role in roles for rp in role.parent_relation.all() if not rp.deleted}
        return ', '.join(str(x) for x in roles | parents)

    class Meta:
        model = User
        exclude = ('password', 'user_permissions', 'is_staff', 'is_superuser', 'groups')
        export_order = (
            'ou',
            'uuid',
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'last_login',
            'date_joined',
            'roles',
        )
        widgets = {
            'roles': {
                'field': 'name',
            },
            'ou': {
                'field': 'name',
            },
        }


class RoleResource(EscapeFormulaMixin, ModelResource):
    members = Field(attribute='members', widget=ListWidget())

    class Meta:
        model = Role
        fields = ('name', 'slug', 'members', 'ou')
        export_order = fields
        widgets = {
            'ou': {
                'field': 'name',
            }
        }
