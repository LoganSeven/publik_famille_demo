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

import decimal
import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core import validators
from django.db import models
from django.template.defaultfilters import floatformat
from django.template.loader import get_template
from django.urls import reverse
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from lingo.invoicing.models.base import (
    AbstractInvoiceLineObject,
    AbstractInvoiceObject,
    get_cancellation_info,
    set_numbers,
)
from lingo.utils.misc import generate_slug


class CreditCancellationReason(models.Model):
    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)
    disabled = models.BooleanField(_('Disabled'), default=False)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

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


class Credit(AbstractInvoiceObject):
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)
    assigned_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    remaining_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    cancelled_at = models.DateTimeField(null=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cancellation_reason = models.ForeignKey(
        CreditCancellationReason, verbose_name=_('Cancellation reason'), on_delete=models.PROTECT, null=True
    )
    cancellation_description = models.TextField(_('Description'), blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        ('assigned_amount__lte', models.F('total_amount')),
                        ('total_amount__gt', 0),
                    ),
                    models.Q(
                        ('assigned_amount__gte', models.F('total_amount')),
                        ('total_amount__lt', 0),
                    ),
                    models.Q(('assigned_amount', 0), ('total_amount', 0)),
                    _connector='OR',
                ),
                name='assigned_amount_check',
            )
        ]

    def set_number(self):
        set_numbers(self, self.date_invoicing or self.created_at, 'credit')

    def normalize(self, for_backoffice=False):
        label = '%s - %s' % (self.formatted_number, self.label)
        if self.remaining_amount:
            amount = _('%(amount)s€') % {'amount': floatformat(self.remaining_amount, 2)}
            label += ' ' + _('(credit left: %s)') % amount
        data = {
            'id': str(self.uuid),
            'label': label,
            'display_id': self.formatted_number,
            'remaining_amount': self.remaining_amount,
            'total_amount': self.total_amount,
            'created': self.date_invoicing or self.created_at.date(),
            'usable': self.usable,
            'has_pdf': True,
        }
        if for_backoffice and self.date_invoicing:
            data.update(
                {
                    'real_created': self.created_at.date(),
                }
            )
        return data

    def html(self):
        template = get_template('lingo/invoicing/credit.html')
        lines_by_user = self.get_lines_by_user()
        amount_by_user = {}
        for user, lines in lines_by_user:
            amount_by_user[user] = sum(li.total_amount for li in lines)
        context = {
            'author': settings.TEMPLATE_VARS.get('global_title'),
            'lang': settings.LANGUAGE_CODE,
            'regie': self.regie,
            'object': self,
            'credit': self,
            'document_model': self.invoice_model,
            'lines_by_user': lines_by_user,
            'amount_by_user': amount_by_user,
            'appearance_settings': self.regie.get_appearance_settings(),
        }
        if context['document_model'] == 'full':
            lines_by_user_for_details = []
            for user, lines in lines_by_user:
                lines_for_details = [li for li in lines if li.description and li.display_description()]
                if lines_for_details:
                    lines_by_user_for_details.append((user, lines_for_details))
            context['lines_by_user_for_details'] = lines_by_user_for_details
        return template.render(context)

    def get_cancellation_info(self):
        return get_cancellation_info(self)

    def get_notification_payload(self, request):
        return {
            'credit_id': str(self.uuid),
            'credit': {
                'id': str(self.uuid),
                'total_amount': self.total_amount,
            },
            'urls': {
                'credit_in_backoffice': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-credit-redirect',
                        kwargs={'credit_uuid': self.uuid},
                    )
                ),
                'credit_pdf': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-credit-pdf-redirect',
                        kwargs={'credit_uuid': self.uuid},
                    )
                ),
            },
            'api_urls': {
                'credit_pdf': request.build_absolute_uri(
                    reverse(
                        'api-invoicing-credit-pdf',
                        kwargs={
                            'regie_identifier': self.regie.slug,
                            'credit_identifier': self.uuid,
                        },
                    )
                ),
            },
        }

    def make_assignments(self, force_assignation=False):
        from lingo.invoicing.models import Invoice, PaymentType

        if not self.usable:
            return
        if not force_assignation and not self.regie.assign_credits_on_creation:
            return
        payment_type, dummy = PaymentType.objects.get_or_create(
            regie=self.regie, slug='credit', defaults={'label': _('Credit')}
        )
        invoice_qs = Invoice.objects.filter(
            basket__isnull=True,
            date_due__gte=now().date(),
            cancelled_at__isnull=True,
            collection__isnull=True,
            remaining_amount__gt=0,
            payer_external_id=self.payer_external_id,
            regie=self.regie,
        ).exclude(pool__campaign__finalized=False)
        for invoice in invoice_qs.order_by('pk'):
            self.refresh_from_db()  # update amounts from db
            if not self.remaining_amount:
                return
            invoice.assign_credit(self, payment_type)


class CreditLine(AbstractInvoiceLineObject):
    credit = models.ForeignKey(Credit, on_delete=models.PROTECT, related_name='lines')

    class Meta:
        indexes = [
            GinIndex(fields=['details']),
            models.Index(fields=['user_external_id']),
        ]


class CreditAssignment(models.Model):
    invoice = models.ForeignKey('invoicing.Invoice', on_delete=models.PROTECT, null=True)
    payment = models.ForeignKey('invoicing.Payment', on_delete=models.PROTECT, null=True)
    refund = models.ForeignKey('invoicing.Refund', on_delete=models.PROTECT, null=True)
    credit = models.ForeignKey(Credit, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)


class Refund(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)
    amount = models.DecimalField(
        max_digits=9, decimal_places=2, validators=[validators.MinValueValidator(decimal.Decimal('0.01'))]
    )
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_email = models.CharField(max_length=250, blank=True)
    payer_phone = models.CharField(max_length=250, blank=True)
    date_refund = models.DateField(_('Refund date'), null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def set_number(self):
        set_numbers(self, self.date_refund or self.created_at, 'refund')

    def normalize(self, for_backoffice=False):
        data = {
            'id': str(self.uuid),
            'display_id': self.formatted_number,
            'amount': self.amount,
            'created': self.date_refund or self.created_at.date(),
            'has_pdf': False,
        }
        if for_backoffice and self.date_refund:
            data.update(
                {
                    'real_created': self.created_at.date(),
                }
            )
        return data

    @property
    def payer_name(self):
        payer_name = '%s %s' % (self.payer_first_name, self.payer_last_name)
        return payer_name.strip()

    @property
    def payer_external_raw_id(self):
        if ':' in self.payer_external_id:
            return self.payer_external_id.split(':')[1]
        return self.payer_external_id

    def get_notification_payload(self, request):
        return {
            'refund_id': str(self.uuid),
            'urls': {
                'refund_in_backoffice': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-refund-redirect',
                        kwargs={'refund_uuid': self.uuid},
                    )
                )
            },
        }
