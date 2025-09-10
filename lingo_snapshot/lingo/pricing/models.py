# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

import copy
import dataclasses
import datetime
import decimal
import uuid

from django.contrib.auth.models import Group
from django.db import models
from django.template import Context, RequestContext, Template, TemplateSyntaxError, VariableDoesNotExist
from django.template.defaultfilters import pprint as django_pprint
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from lingo.agendas.models import Agenda, CheckType
from lingo.export_import.models import WithApplicationMixin
from lingo.pricing import errors
from lingo.snapshot.models import (
    CriteriaCategorySnapshot,
    PricingSnapshot,
    WithSnapshotManager,
    WithSnapshotMixin,
)
from lingo.utils.misc import LingoImportError, WithInspectMixin, clean_import_data, generate_slug
from lingo.utils.wcs import get_wcs_dependencies_from_template


class CriteriaCategory(WithSnapshotMixin, WithApplicationMixin, WithInspectMixin, models.Model):
    # mark temporarily restored snapshots
    snapshot = models.ForeignKey(
        CriteriaCategorySnapshot, on_delete=models.CASCADE, null=True, related_name='temporary_instance'
    )

    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    application_component_type = 'pricing_categories'
    application_label_singular = _('Criteria category')
    application_label_plural = _('Criteria categories')

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

    @property
    def base_slug(self):
        return slugify(self.label)

    def get_dependencies(self):
        return []

    @classmethod
    def import_json(cls, data, snapshot=None):
        criterias = data.pop('criterias', [])
        data = clean_import_data(cls, data)
        qs_kwargs = {}
        if snapshot:
            qs_kwargs = {'snapshot': snapshot}
            data['slug'] = str(uuid.uuid4())  # random slug
        else:
            qs_kwargs = {'slug': data['slug']}
        category, created = cls.objects.update_or_create(defaults=data, **qs_kwargs)

        for criteria in criterias:
            criteria['category'] = category
            Criteria.import_json(criteria)

        return created, category

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'criterias': [a.export_json() for a in self.criterias.all()],
        }

    def get_inspect_keys(self):
        return ['label', 'slug']


class Criteria(WithInspectMixin, models.Model):
    category = models.ForeignKey(
        CriteriaCategory, verbose_name=_('Category'), on_delete=models.CASCADE, related_name='criterias'
    )
    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160)
    condition = models.CharField(_('Condition'), max_length=1000, blank=True)
    order = models.PositiveIntegerField()
    default = models.BooleanField(
        _('Default criteria'), default=False, help_text=_('Will be applied if no other criteria matches')
    )

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['default', 'order']
        unique_together = ['category', 'slug']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if self.default is True:
            self.order = 0
        elif self.order is None:
            max_order = (
                Criteria.objects.filter(category=self.category)
                .aggregate(models.Max('order'))
                .get('order__max')
                or 0
            )
            self.order = max_order + 1
        if not self.slug:
            self.slug = generate_slug(self, category=self.category)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    @classmethod
    def import_json(cls, data):
        data = clean_import_data(cls, data)
        cls.objects.update_or_create(slug=data['slug'], category=data['category'], defaults=data)

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'condition': self.condition,
            'order': self.order,
        }

    def get_inspect_keys(self):
        return ['label', 'slug', 'condition', 'order']

    @property
    def identifier(self):
        return '%s:%s' % (self.category.slug, self.slug)

    def compute_condition(self, context):
        try:
            template = Template('{%% if %s %%}OK{%% endif %%}' % self.condition)
        except TemplateSyntaxError:
            return False
        return template.render(Context(context)) == 'OK'


class PricingCriteriaCategory(models.Model):
    pricing = models.ForeignKey('Pricing', on_delete=models.CASCADE)
    category = models.ForeignKey(CriteriaCategory, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['order']
        unique_together = ['pricing', 'category']

    def save(self, *args, **kwargs):
        if self.order is None:
            max_order = (
                PricingCriteriaCategory.objects.filter(pricing=self.pricing)
                .aggregate(models.Max('order'))
                .get('order__max')
                or 0
            )
            self.order = max_order + 1
        super().save(*args, **kwargs)

    def export_json(self):
        return {
            'category': self.category.slug,
            'order': self.order,
            'criterias': [c.slug for c in self.pricing.criterias.all() if c.category == self.category],
        }

    def duplicate(self, pricing_target):
        new_apcc = copy.deepcopy(self)
        new_apcc.pk = None
        new_apcc.pricing = pricing_target
        new_apcc.save()
        return new_apcc


@dataclasses.dataclass
class PricingMatrixCell:
    criteria: Criteria
    value: decimal.Decimal


@dataclasses.dataclass
class PricingMatrixRow:
    criteria: Criteria
    cells: list[PricingMatrixCell]


@dataclasses.dataclass
class PricingMatrix:
    criteria: Criteria
    rows: list[PricingMatrixRow]


class Pricing(WithSnapshotMixin, WithApplicationMixin, WithInspectMixin, models.Model):
    # mark temporarily restored snapshots
    snapshot = models.ForeignKey(
        PricingSnapshot, on_delete=models.CASCADE, null=True, related_name='temporary_instance'
    )

    label = models.CharField(_('Label'), max_length=150, null=True)
    slug = models.SlugField(_('Identifier'), max_length=160, null=True)

    agendas = models.ManyToManyField(Agenda, related_name='pricings')

    categories = models.ManyToManyField(
        CriteriaCategory,
        related_name='pricings',
        through='PricingCriteriaCategory',
    )
    criterias = models.ManyToManyField(Criteria)

    extra_variables = models.JSONField(blank=True, default=dict)

    date_start = models.DateField(_('Start date'))
    date_end = models.DateField(_('End date'))
    flat_fee_schedule = models.BooleanField(_('Flat fee schedule'), default=False)
    subscription_required = models.BooleanField(_('Subscription is required'), default=True)

    kind = models.CharField(
        _('Kind of pricing'),
        max_length=10,
        choices=[
            ('basic', _('Basic')),
            ('reduction', _('Reduction rate')),
            ('effort', _('Effort rate')),
        ],
        default='basic',
    )
    reduction_rate = models.CharField(
        _('Reduction rate (template)'),
        max_length=1000,
        blank=True,
        help_text=_('The result is expressed as a percentage, and must be between 0 and 100.'),
    )
    effort_rate_target = models.CharField(
        _('Amount to be multiplied by the effort rate (template)'),
        max_length=1000,
        blank=True,
        help_text=_('The result is expressed as an amount, which is then multiplied by the effort rate.'),
    )
    min_pricing = models.DecimalField(
        _('Minimal pricing'), max_digits=9, decimal_places=2, blank=True, null=True
    )
    max_pricing = models.DecimalField(
        _('Maximal pricing'), max_digits=9, decimal_places=2, blank=True, null=True
    )
    accounting_code = models.CharField(
        _('Accounting code (template)'),
        max_length=1000,
        blank=True,
    )

    pricing_data = models.JSONField(null=True)
    min_pricing_data = models.JSONField(null=True)

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    application_component_type = 'pricings'
    application_label_singular = _('Pricing')
    application_label_plural = _('Pricings')

    objects = WithSnapshotManager()
    snapshots = WithSnapshotManager(snapshots=True)

    def __str__(self):
        return '%s - %s' % (
            self.label,
            _('From %(start)s to %(end)s')
            % {'start': self.date_start.strftime('%d/%m/%Y'), 'end': self.date_end.strftime('%d/%m/%Y')},
        )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    def get_dependencies(self):
        yield self.edit_role
        yield self.view_role
        yield from self.agendas.all()
        yield from self.categories.all()
        for value in sorted(self.extra_variables.values()):
            yield from get_wcs_dependencies_from_template(value)
        if self.kind == 'reduction':
            yield from get_wcs_dependencies_from_template(self.reduction_rate)
        if self.kind == 'effort':
            yield from get_wcs_dependencies_from_template(self.effort_rate_target)
        yield from get_wcs_dependencies_from_template(self.accounting_code)

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'agendas': [a.slug for a in self.agendas.all()],
            'date_start': self.date_start.strftime('%Y-%m-%d'),
            'date_end': self.date_end.strftime('%Y-%m-%d'),
            'flat_fee_schedule': self.flat_fee_schedule,
            'subscription_required': self.subscription_required,
            'kind': self.kind,
            'reduction_rate': self.reduction_rate,
            'effort_rate_target': self.effort_rate_target,
            'accounting_code': self.accounting_code,
            'min_pricing': self.min_pricing,
            'max_pricing': self.max_pricing,
            'pricing_data': self.pricing_data,
            'min_pricing_data': self.min_pricing_data,
            'extra_variables': self.extra_variables,
            'billing_dates': [bd.export_json() for bd in self.billingdates.all()],
            'categories': [
                apcc.export_json() for apcc in PricingCriteriaCategory.objects.filter(pricing=self)
            ],
            'permissions': {
                'edit': self.edit_role.name if self.edit_role else None,
                'view': self.view_role.name if self.view_role else None,
            },
        }

    @classmethod
    def import_json(cls, data, snapshot=None):
        data = copy.deepcopy(data)
        agenda_slugs = data.pop('agendas', None) or []
        billing_dates = data.pop('billing_dates', None) or []
        categories = data.pop('categories', [])
        permissions = data.pop('permissions', None) or {}
        categories_by_slug = {c.slug: c for c in CriteriaCategory.objects.all()}
        criterias_by_categories_and_slug = {
            (crit.category.slug, crit.slug): crit
            for crit in Criteria.objects.select_related('category').all()
        }
        for category_data in categories:
            category_slug = category_data['category']
            if category_data['category'] not in categories_by_slug:
                raise LingoImportError(_('Missing "%s" pricing category') % category_data['category'])
            for criteria_slug in category_data['criterias']:
                if (category_slug, criteria_slug) not in criterias_by_categories_and_slug:
                    raise LingoImportError(
                        _('Missing "%(criteria)s" pricing criteria for "%(category)s" category')
                        % {'criteria': criteria_slug, 'category': category_slug}
                    )
        data = clean_import_data(cls, data)
        agendas = []
        for agenda_slug in agenda_slugs:
            try:
                agendas.append(Agenda.objects.get(slug=agenda_slug))
            except Agenda.DoesNotExist:
                raise LingoImportError(_('Missing "%s" agenda') % agenda_slug)
        for role_key in ['edit', 'view']:
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
        pricing, created = cls.objects.update_or_create(defaults=data, **qs_kwargs)
        pricing.agendas.add(*agendas)

        PricingCriteriaCategory.objects.filter(pricing=pricing).delete()
        criterias = []
        for category_data in categories:
            pricing.categories.add(
                categories_by_slug[category_data['category']],
                through_defaults={'order': category_data['order']},
            )
            for criteria_slug in category_data['criterias']:
                criterias.append(criterias_by_categories_and_slug[(category_data['category'], criteria_slug)])
        pricing.criterias.set(criterias)

        for billing_date in billing_dates:
            billing_date['pricing'] = pricing
            BillingDate.import_json(billing_date)

        pricing.refresh_from_db()
        return created, pricing

    def get_inspect_keys(self):
        return ['label', 'slug', 'date_start', 'date_end', 'kind']

    def get_permissions_inspect_fields(self):
        yield from self.get_inspect_fields(keys=['edit_role', 'view_role'])

    def get_settings_inspect_fields(self):
        keys = ['flat_fee_schedule']
        if self.flat_fee_schedule:
            keys += ['subscription_required']
        if self.kind == 'reduction':
            keys += ['reduction_rate']
        elif self.kind == 'effort':
            keys += ['effort_rate_target']
        keys += ['accounting_code']
        yield from self.get_inspect_fields(keys=keys)

    def get_pricing_inspect_fields(self):
        keys = ['pricing_data']
        if self.kind == 'effort':
            keys += ['min_pricing', 'max_pricing']
        elif self.kind == 'reduction':
            keys += ['min_pricing_data']
        yield from self.get_inspect_fields(keys=keys)

    def get_pricing_data_display(self, field='pricing'):
        return mark_safe('<pre>%s</pre>' % django_pprint(self.format_pricing_data(field)))

    def get_min_pricing_data_display(self):
        return self.get_pricing_data_display(field='min_pricing')

    def duplicate(self, label=None, date_start=None, date_end=None):
        # clone current pricing
        new_pricing = copy.deepcopy(self)
        new_pricing.pk = None
        new_pricing.label = label or _('Copy of %s') % self.label
        new_pricing.date_start = date_start or self.date_start
        new_pricing.date_end = date_end or self.date_end
        # reset slug
        new_pricing.slug = None
        new_pricing.save()

        # set criterias
        new_pricing.criterias.set(self.criterias.all())

        # set categories
        for apcc in PricingCriteriaCategory.objects.filter(pricing=self):
            apcc.duplicate(pricing_target=new_pricing)

        return new_pricing

    def _get_user_groups(self, user):
        if not hasattr(user, '_groups'):
            user._groups = user.groups.all()
        return user._groups

    def can_be_managed(self, user):
        if user.is_staff:
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.edit_role_id in group_ids)

    def can_be_viewed(self, user):
        if self.can_be_managed(user):
            return True
        group_ids = [x.id for x in self._get_user_groups(user)]
        return bool(self.view_role_id in group_ids)

    def get_extra_variables(self, request, original_context, bypass_extra_variables=None):
        extra_variables = self.extra_variables or {}
        bypass_extra_variables = bypass_extra_variables or {}
        # ignore unknown keys
        bypass_extra_variables = {k: v for k, v in bypass_extra_variables.items() if k in extra_variables}
        result = bypass_extra_variables
        context = RequestContext(request)
        context.push(original_context)
        for key, tplt in extra_variables.items():
            if key in bypass_extra_variables:
                continue
            try:
                result[key] = Template(tplt).render(context)
            except (TemplateSyntaxError, VariableDoesNotExist):
                continue
        return result

    def get_extra_variables_keys(self):
        return sorted((self.extra_variables or {}).keys())

    def get_pricing_data(
        self, request, pricing_date, user_external_id, payer_external_id, bypass_extra_variables=None
    ):
        # compute pricing for flat_fee_schedule mode
        data = {
            'pricing_date': pricing_date,  # date to use for QF
        }
        context = self.get_pricing_context(
            request=request,
            data=data,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
            bypass_extra_variables=bypass_extra_variables,
        )
        pricing, criterias = self.compute_pricing(context=context)
        pricing, reduction_rate = self.apply_reduction_rate(
            pricing=pricing,
            request=request,
            context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        pricing, effort_rate = self.apply_effort_rate_target(
            value=pricing,
            request=request,
            context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        accounting_code = self.compute_accounting_code(
            request=request,
            original_context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        return {
            'pricing': pricing,
            'calculation_details': {
                'pricing': pricing,
                'criterias': criterias,
                'reduction_rate': reduction_rate,
                'effort_rate': effort_rate,
                'context': context,
            },
            'accounting_code': accounting_code,
        }

    def get_pricing_data_for_event(
        self,
        request,
        agenda,
        event,
        check_status,
        user_external_id,
        payer_external_id,
        bypass_extra_variables=None,
    ):
        # compute pricing for an event
        event_date = datetime.datetime.fromisoformat(event['start_datetime']).date()
        data = {
            'pricing_date': event_date,  # date to use for QF
            'event': event,
        }
        context = self.get_pricing_context(
            request=request,
            data=data,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
            bypass_extra_variables=bypass_extra_variables,
        )
        accounting_code = self.compute_accounting_code(
            request=request,
            original_context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        modifier = self.get_booking_modifier(agenda=agenda, check_status=check_status)
        if modifier['status'] in ['not-booked', 'cancelled']:
            # bypass pricing calculation, we always have the result
            # and invoice line will be not generated
            return {
                'pricing': 0,
                'booking_details': modifier,
                'accounting_code': accounting_code,
            }
        pricing, criterias = self.compute_pricing(context=context)
        pricing, reduction_rate = self.apply_reduction_rate(
            pricing=pricing,
            request=request,
            context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        pricing, effort_rate = self.apply_effort_rate_target(
            value=pricing,
            request=request,
            context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
        return self.aggregate_pricing_data(
            pricing=pricing,
            criterias=criterias,
            reduction_rate=reduction_rate,
            effort_rate=effort_rate,
            context=context,
            modifier=modifier,
            accounting_code=accounting_code,
        )

    def aggregate_pricing_data(
        self, pricing, criterias, reduction_rate, effort_rate, context, modifier, accounting_code
    ):
        if modifier['modifier_type'] == 'fixed':
            pricing_amount = modifier['modifier_fixed']
        else:
            pricing_amount = pricing * modifier['modifier_rate'] / 100
        return {
            'pricing': pricing_amount,
            'calculation_details': {
                'pricing': pricing,
                'criterias': criterias,
                'reduction_rate': reduction_rate,
                'effort_rate': effort_rate,
                'context': context,
            },
            'booking_details': modifier,
            'accounting_code': accounting_code,
        }

    @staticmethod
    def get_pricing(agenda, start_date, flat_fee_schedule):
        try:
            return agenda.pricings.get(
                date_start__lte=start_date,
                date_end__gt=start_date,
                flat_fee_schedule=flat_fee_schedule,
            )
        except (Pricing.DoesNotExist, Pricing.MultipleObjectsReturned):
            raise errors.PricingNotFound

    def get_pricing_context(
        self, request, data, user_external_id, payer_external_id, bypass_extra_variables=None
    ):
        context = {'data': data, 'user_external_id': user_external_id, 'payer_external_id': payer_external_id}
        if ':' in user_external_id:
            context['user_external_raw_id'] = user_external_id.split(':')[1]
        if ':' in payer_external_id:
            context['payer_external_raw_id'] = payer_external_id.split(':')[1]
        return self.get_extra_variables(request, context, bypass_extra_variables=bypass_extra_variables)

    def format_pricing_data(self, field='pricing'):
        # format data to ignore category ordering

        def _format(data):
            if not data and data != 0:
                return

            if not isinstance(data, dict):
                yield [], data
                return

            for criteria, val in data.items():
                result = list(_format(val))
                for criterias, pricing in result:
                    yield [criteria] + criterias, pricing

        return {
            self.format_pricing_data_key(criterias): pricing
            for criterias, pricing in _format(getattr(self, '%s_data' % field))
        }

    def format_pricing_data_key(self, values):
        return '||'.join(sorted(values))

    def set_pricing_data_from_formatted(self, formatted_values, field='pricing'):

        def fill_pricing_data(path, categories_below):
            if not categories_below:
                value = formatted_values.get(self.format_pricing_data_key(path))
                if value is None:
                    return
                return str(value)
            category = categories_below[0]
            data = {}
            for criteria in self.criterias.all():
                new_path = path.copy()
                new_path.append(criteria.identifier)
                if criteria.category_id != category.pk:
                    continue
                value = fill_pricing_data(new_path, categories_below[1:])
                if value is not None:
                    data[criteria.identifier] = value

            return data or None

        categories = self.categories.all().order_by('pricingcriteriacategory__order')[:3]
        pricing_data = fill_pricing_data([], categories)
        setattr(self, '%s_data' % field, pricing_data)

    def _compute_pricing(self, context, field):
        criterias = {}
        categories = []
        # for each category
        for category in self.categories.all():
            criterias[category.slug] = None
            categories.append(category.slug)
            # find the first matching criteria (criterias are ordered)
            for criteria in self.criterias.all():
                if criteria.category_id != category.pk:
                    continue
                if criteria.default:
                    continue
                condition = criteria.compute_condition(context)
                if condition:
                    criterias[category.slug] = criteria.slug
                    break
            if criterias[category.slug] is not None:
                continue
            # if no match, take default criteria if only once defined
            default_criterias = [
                c for c in self.criterias.all() if c.default and c.category_id == category.pk
            ]
            if len(default_criterias) > 1:
                raise errors.MultipleDefaultCriteriaCondition(
                    details={'category': category.slug, 'context': context}
                )
            if not default_criterias:
                raise errors.CriteriaConditionNotFound(
                    details={'category': category.slug, 'context': context}
                )
            criterias[category.slug] = default_criterias[0].slug

        # now search for pricing values matching found criterias
        pricing_data = self.format_pricing_data(field=field)
        pricing = pricing_data.get(
            self.format_pricing_data_key(['%s:%s' % (k, v) for k, v in criterias.items()])
        )
        if pricing is None:
            if field == 'min_pricing':
                raise errors.MinPricingDataError(details={'criterias': criterias, 'context': context})
            raise errors.PricingDataError(details={'criterias': criterias, 'context': context})

        try:
            pricing = decimal.Decimal(pricing)
        except (decimal.InvalidOperation, ValueError, TypeError):
            if field == 'min_pricing':
                raise errors.MinPricingDataFormatError(
                    details={'pricing': pricing, 'wanted': 'decimal', 'context': context}
                )
            raise errors.PricingDataFormatError(
                details={'pricing': pricing, 'wanted': 'decimal', 'context': context}
            )

        if self.kind == 'effort':
            return round(pricing, 4), criterias
        return round(pricing, 2), criterias

    def compute_pricing(self, context):
        return self._compute_pricing(context, 'pricing')

    def compute_min_pricing(self, context):
        return self._compute_pricing(context, 'min_pricing')

    def _compute_template(self, request, original_context, user_external_id, payer_external_id, template):
        context = RequestContext(request)
        context.push(original_context)
        context.push({'user_external_id': user_external_id, 'payer_external_id': payer_external_id})
        if ':' in user_external_id:
            context['user_external_raw_id'] = user_external_id.split(':')[1]
        if ':' in payer_external_id:
            context['payer_external_raw_id'] = payer_external_id.split(':')[1]
        return Template(template).render(context)

    def compute_reduction_rate(self, request, original_context, user_external_id, payer_external_id):
        try:
            reduction_rate = self._compute_template(
                request, original_context, user_external_id, payer_external_id, self.reduction_rate
            )
        except (TemplateSyntaxError, VariableDoesNotExist):
            raise errors.PricingReductionRateError()

        try:
            reduction_rate = decimal.Decimal(reduction_rate)
        except (decimal.InvalidOperation, ValueError, TypeError):
            raise errors.PricingReductionRateFormatError(
                details={'reduction_rate': reduction_rate, 'wanted': 'decimal'}
            )

        if reduction_rate < 0 or reduction_rate > 100:
            raise errors.PricingReductionRateValueError(details={'reduction_rate': reduction_rate})

        return reduction_rate

    def apply_reduction_rate(self, pricing, request, context, user_external_id, payer_external_id):
        if self.kind != 'reduction' or not self.reduction_rate:
            return pricing, {}

        reduction_rate = self.compute_reduction_rate(
            request=request,
            original_context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )

        new_pricing = round(pricing * (100 - reduction_rate) / 100, 2)
        adjusted_pricing = new_pricing
        min_pricing, dummy = self.compute_min_pricing(context=context)
        adjusted_pricing = max(adjusted_pricing, min_pricing)

        return adjusted_pricing, {
            'computed_pricing': pricing,
            'reduction_rate': reduction_rate,
            'reduced_pricing': new_pricing,
            'min_pricing': min_pricing,
            'bounded_pricing': adjusted_pricing,
        }

    def compute_effort_rate_target(self, request, original_context, user_external_id, payer_external_id):
        try:
            effort_rate_target = self._compute_template(
                request, original_context, user_external_id, payer_external_id, self.effort_rate_target
            )
        except (TemplateSyntaxError, VariableDoesNotExist):
            raise errors.PricingEffortRateTargetError()

        try:
            effort_rate_target = decimal.Decimal(effort_rate_target)
        except (decimal.InvalidOperation, ValueError, TypeError):
            raise errors.PricingEffortRateTargetFormatError(
                details={'effort_rate_target': effort_rate_target, 'wanted': 'decimal'}
            )

        if effort_rate_target < 0:
            raise errors.PricingEffortRateTargetValueError(details={'effort_rate_target': effort_rate_target})

        return effort_rate_target

    def apply_effort_rate_target(self, value, request, context, user_external_id, payer_external_id):
        if self.kind != 'effort' or not self.effort_rate_target:
            return value, {}

        effort_rate_target = self.compute_effort_rate_target(
            request=request,
            original_context=context,
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )

        new_pricing = round(value * effort_rate_target / 100, 2)
        adjusted_pricing = new_pricing
        if self.min_pricing is not None:
            adjusted_pricing = max(adjusted_pricing, self.min_pricing)
        if self.max_pricing is not None:
            adjusted_pricing = min(adjusted_pricing, self.max_pricing)

        return adjusted_pricing, {
            'effort_rate': value,
            'effort_rate_target': effort_rate_target,
            'computed_pricing': new_pricing,
            'min_pricing': self.min_pricing,
            'max_pricing': self.max_pricing,
            'bounded_pricing': adjusted_pricing,
        }

    def compute_accounting_code(self, request, original_context, user_external_id, payer_external_id):
        try:
            return self._compute_template(
                request, original_context, user_external_id, payer_external_id, self.accounting_code
            )
        except (TemplateSyntaxError, VariableDoesNotExist):
            raise errors.PricingAccountingCodeError()

    def get_booking_modifier(self, agenda, check_status):
        status = check_status['status']
        if status not in ['error', 'not-booked', 'cancelled', 'presence', 'absence']:
            raise errors.PricingUnknownCheckStatusError(details={'status': status})

        if status == 'error':
            reason = check_status['error_reason']
            # event must be checked
            if reason == 'event-not-checked':
                raise errors.PricingEventNotCheckedError
            # booking must be checked
            if reason == 'booking-not-checked':
                raise errors.PricingBookingNotCheckedError
            # too many bookings found
            if reason == 'too-many-bookings-found':
                raise errors.PricingMultipleBookingError

        # no booking found
        if status == 'not-booked':
            return {
                'status': 'not-booked',
                'modifier_type': 'fixed',
                'modifier_fixed': 0,
            }

        # booking cancelled
        if status == 'cancelled':
            return {
                'status': 'cancelled',
                'modifier_type': 'fixed',
                'modifier_fixed': 0,
            }

        # no check_type, default rates
        if not check_status['check_type']:
            return {
                'status': status,
                'check_type_group': None,
                'check_type': None,
                'modifier_type': 'rate',
                'modifier_rate': 100 if status == 'presence' else 0,
            }

        try:
            check_type = CheckType.objects.get(
                group=agenda.check_type_group_id, slug=check_status['check_type']
            )
        except CheckType.DoesNotExist:
            raise errors.PricingBookingCheckTypeError(
                details={
                    'reason': 'not-found',
                }
            )
        # check_type kind and user_was_present mismatch
        if check_type.kind != status:
            raise errors.PricingBookingCheckTypeError(
                details={
                    'check_type_group': check_type.group.slug,
                    'check_type': check_type.slug,
                    'reason': 'wrong-kind',
                }
            )

        # get pricing modifier
        if check_type.pricing is not None:
            return {
                'status': status,
                'check_type_group': check_type.group.slug,
                'check_type': check_type.slug,
                'modifier_type': 'fixed',
                'modifier_fixed': check_type.pricing,
            }
        if check_type.pricing_rate is not None:
            return {
                'status': status,
                'check_type_group': check_type.group.slug,
                'check_type': check_type.slug,
                'modifier_type': 'rate',
                'modifier_rate': check_type.pricing_rate,
            }
        # pricing not found
        raise errors.PricingBookingCheckTypeError(
            details={
                'check_type_group': check_type.group.slug,
                'check_type': check_type.slug,
                'reason': 'not-configured',
            }
        )

    def _iter_pricing_matrix(self, field):
        categories = self.categories.all().order_by('pricingcriteriacategory__order')[:3]
        pricing_data = self.format_pricing_data(field)

        if not categories:
            return

        if len(categories) < 3:
            yield self.get_pricing_matrix(
                main_criteria=None, categories=categories, pricing_data=pricing_data
            )
            return

        # criterias are ordered
        for criteria in self.criterias.all():
            if criteria.category != categories[0]:
                continue
            yield self.get_pricing_matrix(
                main_criteria=criteria,
                categories=categories[1:],
                pricing_data=pricing_data,
            )

    def iter_pricing_matrix(self):
        return self._iter_pricing_matrix('pricing')

    def iter_min_pricing_matrix(self):
        return self._iter_pricing_matrix('min_pricing')

    def get_pricing_matrix(self, main_criteria, categories, pricing_data):
        matrix = PricingMatrix(
            criteria=main_criteria,
            rows=[],
        )

        def get_pricing_matrix_cell(criteria_2, criteria_3):
            criterias = [main_criteria, criteria_2, criteria_3]
            key = self.format_pricing_data_key([c.identifier for c in criterias if c])
            try:
                value = decimal.Decimal(str(pricing_data.get(key)))
            except (decimal.InvalidOperation, ValueError, TypeError):
                value = None
            return PricingMatrixCell(criteria=criteria_2, value=value)

        if len(categories) < 2:
            criterias_2 = [None]
            criterias_3 = [c for c in self.criterias.all() if c.category == categories[0]]
        else:
            criterias_2 = [c for c in self.criterias.all() if c.category == categories[0]]
            criterias_3 = [c for c in self.criterias.all() if c.category == categories[1]]

        rows = [
            PricingMatrixRow(
                criteria=criteria_3,
                cells=[
                    get_pricing_matrix_cell(
                        criteria_2,
                        criteria_3,
                    )
                    for criteria_2 in criterias_2
                ],
            )
            for criteria_3 in criterias_3
        ]
        matrix.rows = rows

        return matrix


class BillingDate(models.Model):
    pricing = models.ForeignKey(Pricing, on_delete=models.CASCADE, related_name='billingdates')
    date_start = models.DateField(_('Billing start date'))
    label = models.CharField(_('Label'), max_length=150)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return '%s (%s)' % (self.date_start.strftime('%d/%m/%Y'), self.label)

    def export_json(self):
        return {
            'date_start': self.date_start.strftime('%Y-%m-%d'),
            'label': self.label,
        }

    @classmethod
    def import_json(cls, data):
        data = clean_import_data(cls, data)
        cls.objects.update_or_create(pricing=data['pricing'], date_start=data['date_start'], defaults=data)
