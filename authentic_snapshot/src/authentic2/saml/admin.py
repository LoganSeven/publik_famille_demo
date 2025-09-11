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

import logging

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.urls import path
from django.utils.translation import gettext as _

try:
    from django.contrib.contenttypes.admin import GenericTabularInline
except ImportError:
    from django.contrib.contenttypes.generic import GenericTabularInline

from authentic2.attributes_ng.engine import get_service_attributes
from authentic2.saml.models import (
    KeyValue,
    LibertyFederation,
    LibertyProvider,
    LibertyServiceProvider,
    LibertySession,
    SAMLAttribute,
    SPOptionsIdPPolicy,
)

from . import admin_views

logger = logging.getLogger(__name__)


class LibertyServiceProviderInline(admin.StackedInline):
    model = LibertyServiceProvider


class TextAndFileWidget(forms.widgets.MultiWidget):
    def __init__(self, attrs=None):
        widgets = (forms.widgets.Textarea(), forms.widgets.FileInput())
        super().__init__(widgets, attrs)

    def decompress(self, value):
        return (value, None)

    def value_from_datadict(self, data, files, name):
        # If there is a file input use it
        file = self.widgets[1].value_from_datadict(data, files, name + '_1')
        if file:
            file = file.read(file.size).decode()
        if file:
            value = file
        else:
            value = self.widgets[0].value_from_datadict(data, files, name + '_0')
        return value

    def render(self, name, value, attrs=None, **kwargs):
        if attrs is None:
            attrs = {}
        if isinstance(value, str):
            attrs['rows'] = value.count('\n') + 5
            attrs['cols'] = min(max(len(x) for x in value.split('\n')), 150)
        return super().render(name, value, attrs, **kwargs)


class LibertyProviderForm(ModelForm):
    metadata = forms.CharField(required=True, widget=TextAndFileWidget, label=_('Metadata'))

    class Meta:
        model = LibertyProvider
        fields = [
            'name',
            'slug',
            'ou',
            'unauthorized_url',
            'home_url',
            'entity_id',
            'entity_id_sha1',
            'federation_source',
            'metadata_url',
            'metadata',
        ]


def update_metadata(modeladmin, request, queryset):
    qs = queryset.filter(metadata_url__startswith='https://')
    total = qs.count()
    count = 0
    for provider in qs:
        try:
            provider.update_metadata()
        except ValidationError as e:
            params = {'name': provider, 'error_msg': ', '.join(e.messages)}
            messages.error(request, _('Updating SAML provider %(name)s failed: %(error_msg)s') % params)
        else:
            count += 1
    messages.info(
        request, _('%(count)d on %(total)d SAML providers updated') % {'count': count, 'total': total}
    )


class SAMLAttributeInlineForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        service = kwargs.pop('service', None)
        super().__init__(*args, **kwargs)
        choices = list(get_service_attributes(service))
        choices += [('edupersontargetedid', 'eduPersonTargetedId')]
        self.fields['attribute_name'].choices = choices
        self.fields['attribute_name'].widget = forms.Select(choices=choices)

    class Meta:
        model = SAMLAttribute
        fields = [
            'name_format',
            'name',
            'friendly_name',
            'attribute_name',
            'enabled',
        ]


class SAMLAttributeInlineAdmin(GenericTabularInline):
    model = SAMLAttribute
    form = SAMLAttributeInlineForm

    def get_formset(self, request, obj=None, **kwargs):
        # add service argument to form constructor
        class NewForm(self.form):
            def __init__(self, *args, **kwargs):
                kwargs['service'] = obj
                super().__init__(*args, **kwargs)

        kwargs['form'] = NewForm
        return super().get_formset(request, obj=obj, **kwargs)


@admin.register(LibertyProvider)
class LibertyProviderAdmin(admin.ModelAdmin):
    form = LibertyProviderForm
    list_display = ('name', 'ou', 'slug', 'entity_id')
    search_fields = ('name', 'entity_id')
    readonly_fields = ('entity_id', 'protocol_conformance', 'entity_id_sha1', 'federation_source')
    fieldsets = (
        (None, {'fields': ('name', 'slug', 'ou', 'entity_id', 'entity_id_sha1', 'federation_source')}),
        (_('Metadata files'), {'fields': ('metadata_url', 'metadata')}),
    )
    inlines = [
        LibertyServiceProviderInline,
        SAMLAttributeInlineAdmin,
    ]
    actions = [update_metadata]
    prepopulated_fields = {'slug': ('name',)}
    list_filter = (
        'service_provider__sp_options_policy',
        'service_provider__enabled',
    )

    def get_urls(self):
        urls = super().get_urls()
        urls = [
            path(
                'add-from-url/',
                self.admin_site.admin_view(
                    admin_views.AddLibertyProviderFromUrlView.as_view(model_admin=self)
                ),
                name='saml_libertyprovider_add_from_url',
            ),
        ] + urls
        return urls


class LibertyFederationAdmin(admin.ModelAdmin):
    search_fields = ('name_id_content', 'user__username')
    list_display = ('user', 'creation', 'last_modification', 'name_id_content', 'format', 'sp')
    list_filter = ('name_id_format', 'sp')

    def format(self, obj):
        name_id_format = obj.name_id_format
        if name_id_format > 15:
            name_id_format = '\u2026' + name_id_format[-12:]
        return name_id_format


@admin.register(SPOptionsIdPPolicy)
class SPOptionsIdPPolicyAdmin(admin.ModelAdmin):
    inlines = [SAMLAttributeInlineAdmin]
    fields = (
        'name',
        'enabled',
        'prefered_assertion_consumer_binding',
        'encrypt_nameid',
        'encrypt_assertion',
        'authn_request_signed',
        'idp_initiated_sso',
        'default_name_id_format',
        'accepted_name_id_format',
        'ask_user_consent',
        'accept_slo',
        'forward_slo',
        'needs_iframe_logout',
        'iframe_logout_timeout',
        'http_method_for_slo_request',
    )


if settings.DEBUG:
    admin.site.register(LibertyFederation, LibertyFederationAdmin)
    admin.site.register(LibertySession)
    admin.site.register(KeyValue)
