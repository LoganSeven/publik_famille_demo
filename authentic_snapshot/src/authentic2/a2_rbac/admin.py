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

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from . import models


class RoleParentInline(admin.TabularInline):
    model = models.RoleParenting
    fk_name = 'child'
    fields = ['parent']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(direct=True)


class RoleChildInline(admin.TabularInline):
    model = models.RoleParenting
    fk_name = 'parent'
    fields = ['child']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(direct=True)


class RoleAdmin(admin.ModelAdmin):
    inlines = [RoleChildInline, RoleParentInline]
    fields = (
        'uuid',
        'name',
        'slug',
        'description',
        'ou',
        'members',
        'permissions',
        'admin_scope_ct',
        'admin_scope_id',
        'service',
    )
    readonly_fields = ('uuid',)
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('members', 'permissions')
    list_display = ('__str__', 'slug', 'ou', 'service', 'admin_scope')
    list_select_related = True
    list_filter = ['ou', 'service']


class OrganizationalUnitAdmin(admin.ModelAdmin):
    fields = (
        'uuid',
        'name',
        'slug',
        'description',
        'username_is_unique',
        'email_is_unique',
        'phone_is_unique',
        'default',
        'validate_emails',
        'user_can_reset_password',
        'user_add_password_policy',
        'home_url',
        'logo',
        'colour',
    )
    readonly_fields = ('uuid',)
    prepopulated_fields = {'slug': ('name',)}
    list_display = ('name', 'slug')


class PermissionAdmin(admin.ModelAdmin):
    fields = ('operation', 'ou', 'target_ct', 'target_id')
    list_display = ('name', 'operation', 'ou', 'target')
    list_select_related = True

    @admin.display(description=_('name'))
    def name(self, obj):
        return str(obj)


admin.site.register(models.Role, RoleAdmin)
admin.site.register(models.OrganizationalUnit, OrganizationalUnitAdmin)
admin.site.register(models.Permission, PermissionAdmin)
