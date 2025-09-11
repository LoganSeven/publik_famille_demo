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

import xml.etree.ElementTree as ET

import lasso
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import JSONField
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from authentic2.apps.authenticators.models import (
    AddRoleAction,
    AuthenticatorRelatedObjectBase,
    BaseAuthenticator,
)
from authentic2.utils import http
from authentic2.utils.misc import redirect_to_login

NAME_ID_FORMAT_CHOICES = (
    ('', _('None')),
    (
        lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
        _('Persistent (%s)') % lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
    ),
    (
        lasso.SAML2_NAME_IDENTIFIER_FORMAT_TRANSIENT,
        _('Transient (%s)') % lasso.SAML2_NAME_IDENTIFIER_FORMAT_TRANSIENT,
    ),
    (lasso.SAML2_NAME_IDENTIFIER_FORMAT_EMAIL, _('Email (%s)') % lasso.SAML2_NAME_IDENTIFIER_FORMAT_EMAIL),
    (
        lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED,
        _('Unspecified (%s)') % lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED,
    ),
)


def validate_metadata(metadata):
    try:
        doc = ET.fromstring(metadata)
    except (TypeError, ET.ParseError) as e:
        raise ValidationError(_('Cannot parse metadata, %s') % e)

    tag_name = '{%s}EntityDescriptor' % lasso.SAML2_METADATA_HREF
    if doc.tag != tag_name:
        raise ValidationError(_('Invalid metadata, missing tag %s') % tag_name)

    if 'entityID' not in doc.attrib:
        raise ValidationError(_('Invalid metadata, missing entityID'))


class SAMLAuthenticator(BaseAuthenticator):
    metadata_url = models.URLField(_('Metadata URL'), max_length=300, blank=True)
    metadata_cache_time = models.PositiveSmallIntegerField(_('Metadata cache time'), default=3600)
    metadata_http_timeout = models.PositiveSmallIntegerField(_('Metadata HTTP timeout'), default=10)

    metadata = models.TextField(_('Metadata (XML)'), blank=True, validators=[validate_metadata])

    provision = models.BooleanField(_('Create user if their username does not already exists'), default=True)
    verify_ssl_certificate = models.BooleanField(
        _('Verify SSL certificate'),
        default=True,
        help_text=_('Verify SSL certificate when doing HTTP requests, used when resolving artifacts.'),
    )
    transient_federation_attribute = models.CharField(
        _('Transient federation attribute'),
        max_length=64,
        help_text=_(
            'Name of an attribute to use in replacement of the NameID content when the NameID format is transient.'
        ),
        blank=True,
    )
    realm = models.CharField(
        _('Realm (realm)'),
        max_length=32,
        help_text=_('The default realm to associate to user, can be used in username template.'),
        default='saml',
    )
    username_template = models.CharField(
        _('Username template'),
        max_length=128,
        help_text=_(
            'The template to build and/or retrieve a user from its username based '
            'on received attributes, the syntax is the one from the str.format() '
            'method of Python. Available variables are realm, idp (current settings '
            'for the idp issuing the assertion), attributes. The default value is '
            '{attributes[name_id_content]}@{realm}. Another example could be {atttributes[uid][0]} '
            'to set the passed username as the username of the newly created user.'
        ),
        default='{attributes[name_id_content]}@{realm}',
    )
    name_id_policy_format = models.CharField(
        _('NameID policy format'),
        max_length=64,
        choices=NAME_ID_FORMAT_CHOICES,
        help_text=_('The NameID format to request.'),
        blank=True,
    )
    name_id_policy_allow_create = models.BooleanField(_('NameID policy allow create'), default=True)
    force_authn = models.BooleanField(
        _('Force authn'), default=False, help_text=_('Force authentication on each authentication request.')
    )
    add_authnrequest_next_url_extension = models.BooleanField(
        _('Add authnrequest next url extension'), default=False
    )
    group_attribute = models.CharField(
        _('Group attribute'),
        max_length=32,
        help_text=_('Name of the SAML attribute to map to Django group names (for example "role").'),
        blank=True,
    )
    create_group = models.BooleanField(
        _('Create group'), default=True, help_text=_('Create group or only assign existing groups.')
    )
    error_url = models.URLField(
        _('Error URL'),
        help_text=_(
            'URL for the continue link when authentication fails. If not set, the RelayState is '
            'used. If there is no RelayState, application default login redirect URL is used.'
        ),
        blank=True,
    )
    error_redirect_after_timeout = models.PositiveSmallIntegerField(
        _('Error redirect after timeout'),
        default=120,
        help_text=_(
            'Timeout in seconds before automatically redirecting the user to the '
            'continue URL when authentication has failed.'
        ),
    )
    authn_classref = models.CharField(
        _('Authn classref'),
        max_length=512,
        help_text=_(
            'Authorized authentication class references, separated by commas. '
            'Empty value means everything is authorized. Authentication class reference '
            'must be obtained from the identity provider but should come from the '
            'SAML 2.0 specification.'
        ),
        blank=True,
    )
    attribute_mapping = JSONField(
        _('Attribute mapping (deprecated)'),
        default=dict,
        help_text=_(
            'Maps templates based on SAML attributes to field of the user model, '
            'for example {"email": "attributes[mail][0]"}.'
        ),
        blank=True,
    )
    superuser_mapping = JSONField(
        _('Superuser mapping'),
        default=dict,
        editable=False,
        help_text=_(
            'Gives superuser flags to user if a SAML attribute contains a given value, '
            'for example {"roles": "Admin"}.'
        ),
        blank=True,
    )

    type = 'saml'
    how = ['saml']
    manager_view_template_name = 'authentic2_auth_saml/authenticator_detail.html'
    manager_idp_info_template_name = 'authentic2_auth_saml/idp_configuration_info.html'
    description_fields = ['show_condition', 'metadata_url', 'metadata', 'provision']

    class Meta:
        verbose_name = _('SAML')

    @property
    def settings(self):
        settings = {k.upper(): v for k, v in self.__dict__.items()}

        settings['AUTHN_CLASSREF'] = [x.strip() for x in settings['AUTHN_CLASSREF'].split(',') if x.strip()]

        for setting in ('METADATA', 'METADATA_URL'):
            if not settings[setting]:
                del settings[setting]

        settings['LOOKUP_BY_ATTRIBUTES'] = [lookup.as_dict() for lookup in self.attribute_lookups.all()]

        settings['authenticator'] = self
        return settings

    @property
    def manager_form_classes(self):
        from .forms import SAMLAuthenticatorAdvancedForm, SAMLAuthenticatorForm

        return [
            (_('General'), SAMLAuthenticatorForm),
            (_('Advanced'), SAMLAuthenticatorAdvancedForm),
        ]

    @property
    def related_object_form_class(self):
        from .forms import SAMLRelatedObjectForm

        return SAMLRelatedObjectForm

    @property
    def related_models(self):
        return {
            SAMLAttributeLookup: self.attribute_lookups.all(),
            SetAttributeAction: self.set_attribute_actions.all(),
            AddRoleAction: self.add_role_actions.all(),
        }

    def clean(self):
        if not (self.metadata or self.metadata_url):
            raise ValidationError(_('One of the metadata fields must be filled.'))

    def clean_fields(self, exclude=None):
        super().clean_fields(exclude=exclude)

        if self.metadata_url and 'metadata' not in (exclude or []):
            self.metadata = self.load_metadata_url()

    def has_valid_configuration(self, exclude=None):
        exclude = set(exclude or [])
        exclude.add('metadata')
        return super().has_valid_configuration(exclude=exclude) and bool(self.metadata)

    def autorun(self, request, block_id, next_url):
        from .adapters import AuthenticAdapter

        settings = self.settings
        AuthenticAdapter().load_idp(settings, self.order)
        return redirect_to_login(
            request, login_url='mellon_login', params={'entityID': settings['ENTITY_ID'], 'next': next_url}
        )

    def has_signing_key(self):
        return bool(
            getattr(settings, 'MELLON_PRIVATE_KEY', '') and getattr(settings, 'MELLON_PUBLIC_KEYS', '')
        )

    def load_metadata_url(self):
        try:
            response = http.get(
                self.metadata_url,
                timeout=min(self.metadata_http_timeout, 25),
                retries=0,
            )
        except http.HTTPError as e:
            raise ValidationError(_('Metadata URL is unreachable: %s') % e)

        validate_metadata(response.text)
        return response.text

    def refresh_metadata_from_url(self):
        if not self.metadata_url:
            return

        metadata = self.load_metadata_url()

        if self.metadata == metadata:
            return

        self.metadata = metadata
        self.save(update_fields=['metadata'])

    def get_metadata_display(self):
        if not self.metadata:
            return ''

        url = reverse('a2-manager-saml-authenticator-metadata', kwargs={'pk': self.pk})
        return mark_safe('<a href=%s>%s</a>' % (url, _('View metadata')))

    def is_origin_for_user(self, user):
        if not self.set_attribute_actions.filter(user_field='email').exists():
            return False
        if not user.saml_identifiers.exists():
            return False

        from .adapters import AuthenticAdapter

        entity_id = AuthenticAdapter().load_entity_id(self.settings['METADATA'], self.order)
        return user.saml_identifiers.filter(issuer__entity_id=entity_id).exists()

    def login(self, request, *args, **kwargs):
        from . import views

        return views.login(request, self, *args, **kwargs)

    def profile(self, request, *args, **kwargs):
        from . import views

        return views.profile(request, *args, **kwargs)


class SAMLAttributeLookup(AuthenticatorRelatedObjectBase):
    user_field = models.CharField(_('User field'), max_length=256)
    saml_attribute = models.CharField(_('SAML attribute'), max_length=1024)
    ignore_case = models.BooleanField(_('Ignore case'), default=False)

    description = _(
        'Define which attributes are used to establish the link with an identity provider account. '
        'They are tried successively until one matches.'
    )

    class Meta:
        default_related_name = 'attribute_lookups'
        verbose_name = _('Attribute lookup')
        verbose_name_plural = _('Lookup by attributes')

    def __str__(self):
        label = _('"%(saml_attribute)s" (from "%(user_field)s")') % {
            'saml_attribute': self.saml_attribute,
            'user_field': self.get_user_field_display(),
        }
        if self.ignore_case:
            label = '%s, %s' % (label, _('case insensitive'))
        return label

    def as_dict(self):
        return {
            'user_field': self.user_field,
            'saml_attribute': self.saml_attribute,
            'ignore-case': self.ignore_case,
        }

    def get_user_field_display(self):
        from authentic2.forms.widgets import SelectAttributeWidget

        return SelectAttributeWidget.get_options().get(self.user_field, self.user_field)


class SetAttributeAction(AuthenticatorRelatedObjectBase):
    user_field = models.CharField(_('User field'), max_length=256)
    saml_attribute = models.CharField(_('SAML attribute name'), max_length=1024)
    mandatory = models.BooleanField(
        _('Deny login if attribute is missing'),
        default=False,
        help_text=_('Login will also be denied if attribute has more than one value.'),
    )

    description = _('Set user fields using received SAML attributes.')

    class Meta:
        default_related_name = 'set_attribute_actions'
        verbose_name = _('Set an attribute')
        verbose_name_plural = _('Set attributes')

    def __str__(self):
        label = _('"%(attribute)s" from "%(saml_attribute)s"') % {
            'attribute': self.get_user_field_display(),
            'saml_attribute': self.saml_attribute,
        }
        if self.mandatory:
            label = '%s (%s)' % (label, _('mandatory'))
        return label

    def get_user_field_display(self):
        from authentic2.forms.widgets import SelectAttributeWidget

        return SelectAttributeWidget.get_options().get(self.user_field, self.user_field)
