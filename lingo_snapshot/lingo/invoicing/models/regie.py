# lingo - payment and billing system
# Copyright (C) 2025  Entr'ouvert
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
import copy
import uuid

from django.contrib.auth.models import Group
from django.db import models, transaction
from django.template import RequestContext, Template, TemplateSyntaxError, VariableDoesNotExist
from django.utils.encoding import force_str
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from lingo.export_import.models import WithApplicationMixin
from lingo.invoicing import errors
from lingo.invoicing.models.base import DOCUMENT_MODELS
from lingo.snapshot.models import RegieSnapshot, WithSnapshotManager, WithSnapshotMixin
from lingo.utils.fields import RichTextField
from lingo.utils.misc import LingoImportError, WithInspectMixin, clean_import_data, generate_slug
from lingo.utils.wcs import (
    WCSError,
    get_wcs_dependencies_from_template,
    get_wcs_json,
    get_wcs_matching_card_model,
    get_wcs_services,
)


class Regie(WithSnapshotMixin, WithApplicationMixin, WithInspectMixin, models.Model):
    # mark temporarily restored snapshots
    snapshot = models.ForeignKey(
        RegieSnapshot, on_delete=models.CASCADE, null=True, related_name='temporary_instance'
    )

    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)
    with_campaigns = models.BooleanField(_('Regie with invoicing campaigns'), default=False)
    description = models.TextField(
        _('Description'), null=True, blank=True, help_text=_('Optional regie description.')
    )
    edit_role = models.ForeignKey(
        Group,
        blank=True,
        null=True,
        default=None,
        related_name='+',
        verbose_name=_('Edit role'),
        on_delete=models.SET_NULL,
    )
    view_role = models.ForeignKey(
        Group,
        blank=True,
        null=True,
        default=None,
        related_name='+',
        verbose_name=_('View role'),
        on_delete=models.SET_NULL,
    )
    invoice_role = models.ForeignKey(
        Group,
        blank=True,
        null=True,
        default=None,
        related_name='+',
        verbose_name=_('Invoice role'),
        on_delete=models.SET_NULL,
    )
    control_role = models.ForeignKey(
        Group,
        blank=True,
        null=True,
        default=None,
        related_name='+',
        verbose_name=_('Control role'),
        on_delete=models.SET_NULL,
    )
    payer_carddef_reference = models.CharField(_('Card Model'), max_length=150, blank=True)
    payer_cached_carddef_json = models.JSONField(blank=True, default=dict)
    payer_external_id_prefix = models.CharField(
        _('Prefix for payer external id'),
        max_length=250,
        blank=True,
    )
    payer_external_id_template = models.CharField(
        _('Template for payer external id'),
        max_length=1000,
        help_text=_('To get payer external id from user external id'),
        blank=True,
    )
    payer_external_id_from_nameid_template = models.CharField(
        _('Template for payer external id from nameid'),
        max_length=1000,
        help_text='Example of templated value: {{ cards|objects:"adults"|filter_by_user:nameid|first|get:"id"|default:"" }}',
        blank=True,
    )
    payer_user_fields_mapping = models.JSONField(blank=True, default=dict)
    assign_credits_on_creation = models.BooleanField(
        _('Use a credit when created to pay old invoices'), default=True
    )

    counter_name = models.CharField(
        _('Counter name'),
        default='{yy}',
        max_length=50,
    )
    invoice_number_format = models.CharField(
        _('Invoice number format'),
        default='F{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )
    collection_number_format = models.CharField(
        _('Invoice collection docket number format'),
        default='T{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )
    payment_number_format = models.CharField(
        _('Payment number format'),
        default='R{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )
    docket_number_format = models.CharField(
        _('Payment docket number format'),
        default='B{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )
    credit_number_format = models.CharField(
        _('Credit number format'),
        default='A{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )
    refund_number_format = models.CharField(
        _('Refund number format'),
        default='V{regie_id:02d}-{yy}-{mm}-{number:07d}',
        max_length=100,
    )

    main_colour = models.CharField(_('Main colour in documents'), max_length=7, default='#DF5A13')
    invoice_model = models.CharField(
        _('Invoice model'),
        max_length=10,
        choices=DOCUMENT_MODELS,
        default='middle',
    )
    invoice_custom_text = RichTextField(
        _('Custom text in invoice'), blank=True, null=True, help_text=_('Displayed in footer.')
    )
    certificate_model = models.CharField(
        _('Payments certificate model'),
        max_length=10,
        choices=DOCUMENT_MODELS,
        blank=True,
    )
    controller_name = models.CharField(_('Controller name'), max_length=256, blank=True)
    city_name = models.CharField(_('City name'), max_length=256, blank=True)
    custom_logo = models.ImageField(
        verbose_name=_('Custom Logo'),
        upload_to='logo',
        blank=True,
        null=True,
    )
    custom_address = RichTextField(
        verbose_name=_('Custom Address'),
        blank=True,
        null=True,
    )
    custom_invoice_extra_info = RichTextField(
        verbose_name=_('Custom invoice extra information'),
        blank=True,
        null=True,
        help_text=_('Displayed below the address block.'),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    application_component_type = 'regies'
    application_label_singular = _('Regie')
    application_label_plural = _('Regies')

    objects = WithSnapshotManager()
    snapshots = WithSnapshotManager(snapshots=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

        if 'update_fields' in kwargs:
            # don't populate the cache
            return

        def populate_cache():
            if self.payer_carddef_reference:
                parts = self.payer_carddef_reference.split(':')
                wcs_key, card_slug = parts[:2]
                wcs_site = get_wcs_services().get(wcs_key)
                try:
                    card_schema = get_wcs_json(
                        wcs_site, 'api/cards/%s/@schema' % card_slug, log_errors='warn'
                    )
                except WCSError:
                    return

                if not card_schema:
                    return

                self.payer_cached_carddef_json = card_schema
                self.save(update_fields=['payer_cached_carddef_json'])

        populate_cache()

    @property
    def base_slug(self):
        return slugify(self.label)

    def get_dependencies(self):
        yield self.edit_role
        yield self.view_role
        yield self.invoice_role
        yield self.control_role
        if self.payer_carddef_reference:
            parts = self.payer_carddef_reference.split(':')
            wcs_key, card_slug = parts[:2]
            wcs_site_url = get_wcs_services().get(wcs_key)['url']
            urls = {
                'export': f'{wcs_site_url}api/export-import/cards/{card_slug}/',
                'dependencies': f'{wcs_site_url}api/export-import/cards/{card_slug}/dependencies/',
                'redirect': f'{wcs_site_url}api/export-import/cards/{card_slug}/redirect/',
            }
            yield {
                'type': 'cards',
                'id': card_slug,
                'text': self.payer_cached_carddef_json.get('name'),
                'urls': urls,
            }
        yield from get_wcs_dependencies_from_template(self.payer_external_id_template)
        yield from get_wcs_dependencies_from_template(self.payer_external_id_from_nameid_template)

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'with_campaigns': self.with_campaigns,
            'description': self.description,
            'permissions': {
                'edit': self.edit_role.name if self.edit_role else None,
                'view': self.view_role.name if self.view_role else None,
                'invoice': self.invoice_role.name if self.invoice_role else None,
                'control': self.control_role.name if self.control_role else None,
            },
            'payer_carddef_reference': self.payer_carddef_reference,
            'payer_external_id_prefix': self.payer_external_id_prefix,
            'payer_external_id_template': self.payer_external_id_template,
            'payer_external_id_from_nameid_template': self.payer_external_id_from_nameid_template,
            'payer_user_fields_mapping': self.payer_user_fields_mapping,
            'assign_credits_on_creation': self.assign_credits_on_creation,
            'counter_name': self.counter_name,
            'invoice_number_format': self.invoice_number_format,
            'collection_number_format': self.collection_number_format,
            'payment_number_format': self.payment_number_format,
            'docket_number_format': self.docket_number_format,
            'credit_number_format': self.credit_number_format,
            'refund_number_format': self.refund_number_format,
            'main_colour': self.main_colour,
            'invoice_model': self.invoice_model,
            'invoice_custom_text': self.invoice_custom_text,
            'certificate_model': self.certificate_model,
            'controller_name': self.controller_name,
            'city_name': self.city_name,
            'payment_types': [p.export_json() for p in self.paymenttype_set.all()],
        }

    @classmethod
    def import_json(cls, data, snapshot=None):
        data = copy.deepcopy(data)
        payment_types = data.pop('payment_types', [])
        permissions = data.pop('permissions', None) or {}
        data = clean_import_data(cls, data)

        for role_key in ['edit', 'view', 'invoice', 'control']:
            role_name = permissions.get(role_key)
            if role_name:
                try:
                    data[f'{role_key}_role'] = Group.objects.get(name=role_name)
                except Group.DoesNotExist:
                    raise LingoImportError('Missing role: %s' % role_name)

        qs_kwargs = {}
        if snapshot:
            qs_kwargs = {'snapshot': snapshot}
            data['slug'] = str(uuid.uuid4())  # random slug
        else:
            qs_kwargs = {'slug': data['slug']}
        regie, created = cls.objects.update_or_create(defaults=data, **qs_kwargs)

        for payment_type in payment_types:
            payment_type['regie'] = regie
            PaymentType.import_json(payment_type)

        return created, regie

    def get_inspect_keys(self):
        return ['label', 'slug', 'description']

    def get_permissions_inspect_fields(self):
        yield from self.get_inspect_fields(keys=['edit_role', 'view_role', 'invoice_role', 'control_role'])

    def get_settings_inspect_fields(self):
        keys = ['with_campaigns', 'assign_credits_on_creation']
        yield from self.get_inspect_fields(keys=keys)

    def get_payer_inspect_fields(self):
        keys = []
        if self.with_campaigns:
            keys = [
                'payer_carddef_reference',
                'payer_external_id_prefix',
                'payer_external_id_template',
            ]
        keys += [
            'payer_external_id_from_nameid_template',
        ]
        yield from self.get_inspect_fields(keys=keys)

    def get_counters_inspect_fields(self):
        keys = [
            'counter_name',
            'invoice_number_format',
            'collection_number_format',
            'payment_number_format',
            'docket_number_format',
            'credit_number_format',
            'refund_number_format',
        ]
        yield from self.get_inspect_fields(keys=keys)

    def get_publishing_inspect_fields(self):
        keys = [
            'main_colour',
            'invoice_model',
            'invoice_custom_text',
            'certificate_model',
            'controller_name',
            'city_name',
        ]
        yield from self.get_inspect_fields(keys=keys)

    def get_counter_name(self, invoice_date):
        return self.counter_name.format(
            yyyy=invoice_date.strftime('%Y'),
            yy=invoice_date.strftime('%y'),
            mm=invoice_date.strftime('%m'),
        )

    def format_number(self, invoice_date, invoice_number, kind):
        number_format = getattr(self, '%s_number_format' % kind)
        return number_format.format(
            yyyy=invoice_date.strftime('%Y'),
            yy=invoice_date.strftime('%y'),
            mm=invoice_date.strftime('%m'),
            number=invoice_number,
            regie_id=self.pk,
        )

    def _get_user_groups(self, user):
        if not hasattr(user, '_groups'):
            user._groups = user.groups.all()
        return user._groups

    def can_be_managed(self, user):
        if user.is_staff:
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.edit_role_id in group_ids)

    def can_be_invoiced(self, user):
        if user.is_staff:
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.invoice_role_id in group_ids)

    def can_be_controlled(self, user):
        if user.is_staff:
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.control_role_id in group_ids)

    def can_be_viewed(self, user):
        if self.can_be_managed(user) or self.can_be_invoiced(user) or self.can_be_controlled(user):
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.view_role_id in group_ids)

    @property
    def payer_carddef_name(self):
        if not self.payer_carddef_reference:
            return
        result = get_wcs_matching_card_model(self.payer_carddef_reference)
        if not result:
            return
        return result

    @property
    def payer_carddef_fields(self):
        if not self.payer_cached_carddef_json:
            return
        return {
            f['varname']: f['label']
            for f in self.payer_cached_carddef_json.get('fields')
            if f.get('varname') and f['type'] != 'page'
        }

    @property
    def payer_user_variables(self):
        return [
            ('first_name', _('First name')),
            ('last_name', _('Last name')),
            ('address', _('Address')),
            ('email', _('Email')),
            ('phone', _('Phone')),
            ('direct_debit', _('Direct debit')),
        ]

    @property
    def payer_user_fields(self):
        for key, label in self.payer_user_variables:
            value = ''
            if self.payer_user_fields_mapping.get(key):
                varname = self.payer_user_fields_mapping.get(key)
                value = self.payer_carddef_fields.get(varname) or ''
            yield label, value

    def _get_payer_external_id(self, request, original_context, template_key):
        context = RequestContext(request)
        context.push(original_context)
        tplt = getattr(self, template_key) or ''
        if not tplt:
            raise errors.PayerError(details={'reason': 'empty-template'})
        try:
            value = Template(tplt).render(context)
            if not value:
                raise errors.PayerError(details={'reason': 'empty-result'})
            return '%s%s' % (self.payer_external_id_prefix, value)
        except TemplateSyntaxError:
            raise errors.PayerError(details={'reason': 'syntax-error'})
        except VariableDoesNotExist:
            raise errors.PayerError(details={'reason': 'variable-error'})

    def get_payer_external_id(self, request, user_external_id, booking=None):
        context = {'user_external_id': user_external_id, 'data': {'booking': booking or {}}}
        if ':' in user_external_id:
            context['user_external_raw_id'] = user_external_id.split(':')[1]
        return self._get_payer_external_id(
            request=request,
            original_context=context,
            template_key='payer_external_id_template',
        )

    def get_payer_external_id_from_nameid(self, request, nameid):
        context = {'nameid': nameid}
        return self._get_payer_external_id(
            request=request,
            original_context=context,
            template_key='payer_external_id_from_nameid_template',
        )

    def get_payer_data(self, request, payer_external_id):
        if not self.payer_carddef_reference:
            raise errors.PayerError(details={'reason': 'missing-card-model'})
        result = {}
        context = RequestContext(request, autoescape=False)
        payer_external_raw_id = None
        if ':' in payer_external_id:
            payer_external_raw_id = payer_external_id.split(':')[1]
        context.push({'payer_external_id': payer_external_raw_id or payer_external_id})
        bool_keys = ['direct_debit']
        not_required_keys = ['email', 'phone']
        for key, dummy in self.payer_user_variables:
            if not self.payer_user_fields_mapping.get(key):
                if key in bool_keys:
                    tplt = 'False'
                elif key in not_required_keys:
                    tplt = ''
                else:
                    raise errors.PayerDataError(details={'key': key, 'reason': 'not-defined'})
            else:
                tplt = (
                    '{{ cards|objects:"%s"|filter_by_internal_id:payer_external_id|include_fields|first|get:"fields"|get:"%s"|default:"" }}'
                    % (
                        self.payer_carddef_reference.split(':')[1],
                        self.payer_user_fields_mapping[key],
                    )
                )
            value = Template(tplt).render(context)
            if not value:
                if key in bool_keys:
                    value = False
                elif key not in not_required_keys:
                    raise errors.PayerDataError(details={'key': key, 'reason': 'empty-result'})
            if key in bool_keys:
                if value in ('True', 'true', '1'):
                    value = True
                elif value in ('False', 'false', '0'):
                    value = False
                if not isinstance(value, bool):
                    raise errors.PayerDataError(details={'key': key, 'reason': 'not-a-boolean'})
            result[key] = value
        return result

    def get_appearance_settings(self):
        default_settings = AppearanceSettings.singleton()
        default_settings.logo = self.custom_logo or default_settings.logo
        default_settings.address = self.custom_address or default_settings.address
        default_settings.extra_info = self.custom_invoice_extra_info or default_settings.extra_info
        return default_settings


class AppearanceSettings(models.Model):
    logo = models.ImageField(
        verbose_name=_('Logo'),
        upload_to='logo',
        blank=True,
        null=True,
    )
    address = RichTextField(
        verbose_name=_('Address'),
        blank=True,
        null=True,
    )
    extra_info = RichTextField(
        verbose_name=_('Additional information'),
        blank=True,
        null=True,
        help_text=_('Displayed below the address block.'),
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    @classmethod
    def singleton(cls):
        return cls.objects.first() or cls()

    def logo_base64_encoded(self):
        return force_str(base64.encodebytes(self.logo.read()))


DEFAULT_PAYMENT_TYPES = [
    ('credit', _('Credit')),
    ('creditcard', _('Credit card')),
    ('cash', _('Cash')),
    ('check', _('Check')),
    ('directdebit', _('Direct debit')),
    ('online', _('Online')),
    ('cesu', _('CESU')),
    ('holidaycheck', _('Holiday check')),
]


class PaymentType(WithInspectMixin, models.Model):
    regie = models.ForeignKey(Regie, on_delete=models.PROTECT)
    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160)
    disabled = models.BooleanField(_('Disabled'), default=False)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['label']
        unique_together = ['regie', 'slug']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    @classmethod
    def create_defaults(cls, regie):
        for slug, label in DEFAULT_PAYMENT_TYPES:
            cls.objects.get_or_create(regie=regie, slug=slug, defaults={'label': label})

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'disabled': self.disabled,
        }

    @classmethod
    def import_json(cls, data):
        data = copy.deepcopy(data)
        data = clean_import_data(cls, data)

        payment_type, created = cls.objects.update_or_create(
            slug=data['slug'], regie=data['regie'], defaults=data
        )
        return created, payment_type

    def get_inspect_keys(self):
        return ['label', 'slug', 'disabled']


class Counter(models.Model):
    regie = models.ForeignKey(Regie, on_delete=models.PROTECT)
    name = models.CharField(max_length=128)
    value = models.PositiveIntegerField(default=0)
    kind = models.CharField(
        max_length=10,
        choices=[
            ('invoice', _('Invoice')),
            ('collection', _('Collection docket')),
            ('payment', _('Payment')),
            ('credit', _('Credit')),
            ('refund', _('Refund')),
            ('docket', _('Payment Docket')),
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        unique_together = (('regie', 'name', 'kind'),)

    @classmethod
    def get_count(cls, regie, name, kind):
        with transaction.atomic():
            queryset = cls.objects.select_for_update()
            counter, dummy = queryset.get_or_create(regie=regie, name=name, kind=kind)
            counter.value += 1
            counter.save()
        return counter.value
