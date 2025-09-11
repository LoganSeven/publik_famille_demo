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

import xml.etree.ElementTree as ET

import requests
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.compat_lasso import lasso

from .models import LibertyProvider, LibertyServiceProvider


class AddLibertyProviderFromUrlForm(forms.Form):
    name = forms.CharField(max_length=140, label=_('Name'))
    slug = forms.SlugField(
        max_length=140, label=_('Shortcut'), help_text=_('Internal nickname for the service provider')
    )
    url = forms.URLField(label=_("Metadata's URL"))
    ou = forms.ModelChoiceField(
        queryset=OrganizationalUnit.objects, initial=get_default_ou, label=_('Organizational unit')
    )

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        slug = cleaned_data.get('slug')
        url = cleaned_data.get('url')
        ou = cleaned_data.get('ou')
        self.instance = None
        self.childs = []
        if name and slug and url:
            try:
                response = requests.get(url, timeout=settings.REQUESTS_TIMEOUT)
                response.raise_for_status()
                content = force_str(response.content)
            except requests.RequestException as e:
                raise ValidationError(
                    _('Retrieval of %(url)s failed: %(exception)s') % {'url': url, 'exception': e}
                )
            root = ET.fromstring(content)
            if root.tag != '{%s}EntityDescriptor' % lasso.SAML2_METADATA_HREF:
                raise ValidationError(_('Invalid SAML metadata: %s') % _('missing EntityDescriptor tag'))
            is_sp = not root.find('{%s}SPSSODescriptor' % lasso.SAML2_METADATA_HREF) is None
            if not is_sp:
                raise ValidationError(_('Invalid SAML metadata: %s') % _('missing SPSSODescriptor tags'))
            liberty_provider = LibertyProvider(
                name=name, slug=slug, metadata=content, metadata_url=url, ou=ou
            )
            liberty_provider.full_clean(exclude=('entity_id', 'protocol_conformance'))
            self.childs.append(LibertyServiceProvider(liberty_provider=liberty_provider, enabled=True))
            self.instance = liberty_provider
        return cleaned_data

    def save(self):
        if self.instance is not None:
            self.instance.save()
            for child in self.childs:
                child.liberty_provider = self.instance
                child.save()
        return self.instance
