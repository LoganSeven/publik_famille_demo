# lingo - payment and billing system
# Copyright (C) 2022-2023  Entr'ouvert
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

import uuid

import eopayment
from django.contrib.auth.models import Group
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from lingo.invoicing.models import Invoice, Payment, PaymentType, Regie
from lingo.utils.misc import generate_slug

SERVICES = [
    (eopayment.DUMMY, _('Dummy (for tests)')),
    (eopayment.SYSTEMPAY, 'systempay (Banque Populaire)'),
    (eopayment.SIPS2, _('SIPS (Atos, other countries)')),
    (eopayment.OGONE, _('Ingenico (formerly Ogone)')),
    (eopayment.PAYBOX, 'Paybox'),
    (eopayment.PAYZEN, 'PayZen'),
    (eopayment.PAYFIP_WS, 'PayFiP Régie Web-Service'),
    (eopayment.KEYWARE, 'Keyware'),
    (eopayment.MOLLIE, 'Mollie'),
    (eopayment.SAGA, 'Saga/PayFiP Régie (Futur System)'),
    (eopayment.WORLDLINE, 'Atos Worldline'),
]


class PaymentBackend(models.Model):
    label = models.CharField(verbose_name=_('Label'), max_length=64)
    slug = models.SlugField(
        unique=True,
        verbose_name=_('Identifier'),
        help_text=_('The identifier is used in webservice calls and callback URLs for the payment backend.'),
    )
    service = models.CharField(verbose_name=_('Payment Service'), max_length=64, choices=SERVICES)
    service_options = models.JSONField(blank=True, default=dict, verbose_name=_('Payment Service Options'))
    regie = models.OneToOneField(Regie, null=True, on_delete=models.SET_NULL)
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

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return str(self.label)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

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

    @property
    def eopayment(self):
        return eopayment.Payment(self.service, self.service_options or {})

    def check_payment_is_possible_code(self, amount):
        if self.eopayment.get_minimal_amount() is not None and amount < self.eopayment.get_minimal_amount():
            return 'amount-to-low'
        elif (
            self.eopayment.get_maximal_amount() is not None and amount >= self.eopayment.get_maximal_amount()
        ):
            return 'amount-to-high'

    def check_payment_is_possible(self, amount):
        error_messages = {
            'amount-to-low': _('The amount is too low to be paid online.'),
            'amount-to-high': _('The amount is too high to be paid online.'),
        }
        code = self.check_payment_is_possible_code(amount)
        return error_messages.get(code)

    @property
    def service_parameters(self):
        return [
            p
            for p in self.eopayment.get_parameters(scope='global')
            if p['name'] not in ('normal_return_url', 'automatic_return_url') and not p.get('deprecated')
        ]

    def make_eopayment(self, request=None, transaction_id=None, **kwargs):
        options = self.service_options or {}
        if not isinstance(options, dict):
            options = {}
        if request and transaction_id:
            options['normal_return_url'] = request.build_absolute_uri(
                reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction_id})
            )
            options['automatic_return_url'] = request.build_absolute_uri(
                reverse('lingo-epayment-explicit-callback', kwargs={'transaction_id': transaction_id})
            )
        options.update(**kwargs)
        return eopayment.Payment(self.service, options)


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    start_date = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    end_date = models.DateTimeField(null=True)
    bank_data = models.JSONField(blank=True, default=dict)
    order_id = models.CharField(max_length=200)
    bank_transaction_id = models.CharField(max_length=200, null=True)
    bank_transaction_date = models.DateTimeField(blank=True, null=True)
    status = models.IntegerField(null=True)
    amount = models.DecimalField(default=0, max_digits=7, decimal_places=2)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True)
    backend = models.ForeignKey(PaymentBackend, on_delete=models.SET_NULL, null=True)
    next_url = models.TextField(null=True)

    RUNNING_STATUSES = [0, eopayment.WAITING, eopayment.RECEIVED]
    PAID_STATUSES = [eopayment.PAID, eopayment.ACCEPTED]
    OTHER_STATUSES = [eopayment.DENIED, eopayment.CANCELLED, eopayment.EXPIRED, eopayment.ERROR]

    class Meta:
        indexes = [
            models.Index(
                'status',
                models.F('start_date').desc(),
                name='transaction_status_date_idx',
            )
        ]

    def status_label(self):
        return {
            0: _('Started'),
            eopayment.RECEIVED: _('Started'),
            eopayment.ACCEPTED: _('Paid (accepted)'),
            eopayment.PAID: _('Paid'),
            eopayment.DENIED: _('Denied'),
            eopayment.CANCELLED: _('Cancelled'),
            eopayment.WAITING: _('Waiting'),
            eopayment.EXPIRED: _('Expired'),
            eopayment.ERROR: _('Error'),
        }.get(self.status) or _('Unknown (%s)') % self.status

    def is_paid(self):
        return self.status in self.PAID_STATUSES

    def is_running(self):
        return self.status in self.RUNNING_STATUSES

    def check_status(self):
        eop = self.backend.make_eopayment()
        if eop.has_payment_status:
            try:
                payment_response = eop.payment_status(self.order_id, transaction_date=self.start_date)
            except eopayment.PaymentException:
                pass
            else:
                self.handle_backend_response(payment_response)

    def handle_backend_response(self, response):
        if self.status == response.result:
            # return early if self status didn't change (it means the
            # payment service sent the response both as server to server and
            # via the user browser and we already handled one).
            return

        assert response.signed

        self.status = response.result
        self.bank_transaction_id = response.transaction_id
        self.bank_data = response.bank_data
        self.end_date = now()
        # store transaction_date but prevent multiple updates
        if self.bank_transaction_date is None:
            self.bank_transaction_date = response.transaction_date
        self.save()
        if self.is_paid() and self.invoice:
            payment_type, dummy = PaymentType.objects.get_or_create(
                regie=self.backend.regie, slug='online', defaults={'label': _('Online')}
            )
            Payment.make_payment(
                regie=self.backend.regie,
                amount=self.amount,
                payment_type=payment_type,
                invoices=[self.invoice],
                transaction_id=self.order_id,
                transaction_date=self.end_date,
                order_id=self.order_id,
                bank_transaction_id=self.bank_transaction_id,
                bank_transaction_date=self.bank_transaction_date,
                bank_data=self.bank_data,
            )
            from lingo.basket.models import Basket

            Basket.signal_paid_invoice(self.invoice)

        elif not self.is_running() and self.invoice:
            # transaction no longer running, mark the basket as ready to be retried
            from lingo.basket.models import Basket

            Basket.signal_reopen(self.invoice)
