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

from django import forms
from django.contrib import admin
from django.utils.translation import gettext as _

from authentic2.admin import CleanupAdminMixin
from authentic2.attributes_ng.engine import get_attribute_names
from authentic2.decorators import to_iter

from . import models


class ServiceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = self.choices({'user': None, 'request': None, 'service': self.instance})
        self.fields['identifier_attribute'].choices = choices
        self.fields['identifier_attribute'].widget = forms.Select(choices=choices)

    @to_iter
    def choices(self, ctx):
        return [('', _('None'))] + get_attribute_names(ctx)

    class Meta:
        model = models.Service
        fields = '__all__'


class AttributeInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        service = kwargs.pop('service', None)
        super().__init__(*args, **kwargs)
        choices = self.choices({'user': None, 'request': None, 'service': service})
        self.fields['attribute_name'].choices = choices
        self.fields['attribute_name'].widget = forms.Select(choices=choices)

    @to_iter
    def choices(self, ctx):
        return [('', _('None'))] + get_attribute_names(ctx)

    class Meta:
        model = models.Attribute
        fields = [
            'slug',
            'attribute_name',
            'enabled',
        ]


class AttributeInlineAdmin(admin.TabularInline):
    model = models.Attribute
    form = AttributeInlineForm

    def get_formset(self, request, obj=None, **kwargs):
        # add service argument to form constructor
        class NewForm(self.form):
            def __init__(self, *args, **kwargs):
                kwargs['service'] = obj
                super().__init__(*args, **kwargs)

        kwargs['form'] = NewForm
        return super().get_formset(request, obj=obj, **kwargs)


class ServiceAdmin(admin.ModelAdmin):
    form = ServiceForm
    list_display = ('name', 'ou', 'slug', 'urls', 'identifier_attribute')
    prepopulated_fields = {'slug': ('name',)}
    fieldsets = (
        (
            None,
            {
                'fields': [
                    'name',
                    'slug',
                    'ou',
                    'unauthorized_url',
                    'home_url',
                    'urls',
                    'identifier_attribute',
                    'proxy',
                ]
            },
        ),
        (
            _('Logout'),
            {
                'fields': [
                    'logout_url',
                    'logout_use_iframe',
                    'logout_use_iframe_timeout',
                ]
            },
        ),
    )
    inlines = [AttributeInlineAdmin]


class TicketAdmin(CleanupAdminMixin, admin.ModelAdmin):
    list_display = ('ticket_id', 'validity', 'renew', 'service', 'service_url', 'user', 'creation', 'expire')


admin.site.register(models.Service, ServiceAdmin)

admin.site.register(models.Ticket, TicketAdmin)
