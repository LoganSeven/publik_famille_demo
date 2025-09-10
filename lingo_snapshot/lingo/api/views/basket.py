# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

import datetime

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop as N_
from rest_framework.views import APIView

from lingo.api import serializers
from lingo.api.utils import APIAdmin, APIErrorBadRequest, Response
from lingo.api.views.utils import FromBookingsMixin
from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.invoicing.models import DraftInvoice, DraftInvoiceLine, Regie


class BasketsView(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketSerializer

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data, regie=regie)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        basket = Basket.objects.filter(
            regie=regie,
            payer_nameid=serializer.validated_data['payer_nameid'],
            status='open',
        ).first()
        # open basket already exists in the regie, return this one
        if basket is not None:
            return Response({'data': {'basket_id': str(basket.uuid)}})

        today = now().date()
        invoice = DraftInvoice.objects.create(
            regie=regie,
            label=_('Invoice from %s') % today.strftime('%d/%m/%Y'),
            date_publication=today,
            date_payment_deadline=today + datetime.timedelta(days=1),
            date_due=today + datetime.timedelta(days=1),
            payer_direct_debit=False,
            origin='basket',
            **{
                k: v
                for k, v in serializer.validated_data.items()
                if k.startswith('payer') and k != 'payer_nameid'
            },
        )
        basket = Basket.objects.create(
            regie=regie,
            draft_invoice=invoice,
            expiry_at=now() + datetime.timedelta(minutes=settings.BASKET_EXPIRY_DELAY),
            **serializer.validated_data,
        )

        return Response({'data': {'basket_id': str(basket.uuid)}})


basket_baskets = BasketsView.as_view()


class BasketCheckView(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketCheckSerializer

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.query_params, regie=regie)
        if not serializer.is_valid():
            err_class = ''
            for errors in serializer.errors.values():
                for error in errors:
                    if error.code in [
                        'payer_active_basket',
                        'user_existing_line',
                        'payer_active_basket_to_pay',
                    ]:
                        err_class = error.code
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors, err_class=err_class)

        return Response({'err': 0})


basket_basket_check = BasketCheckView.as_view()


class BasketLinesView(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketLineSerializer

    def post(self, request, regie_identifier, basket_identifier):
        basket = get_object_or_404(
            Basket, uuid=basket_identifier, regie__slug=regie_identifier, status='open'
        )
        serializer = self.serializer_class(data=request.data, basket=basket)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        params = serializer.validated_data
        reuse = params.pop('reuse')
        if reuse:
            user_external_id = params.pop('user_external_id')
            line, dummy = BasketLine.objects.get_or_create(
                basket=basket, user_external_id=user_external_id, defaults=params
            )
            if line.closed:
                line.closed = False
                line.save()
        else:
            line = BasketLine.objects.create(basket=basket, **params)

        return Response({'data': {'line_id': str(line.uuid), 'closed': line.closed}})


basket_basket_lines = BasketLinesView.as_view()


class BasketLinesFromBookingsView(FromBookingsMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketLinesFromBookingsSerializer

    def post_init(self, request, regie_identifier, basket_identifier):
        self.regie = get_object_or_404(Regie, slug=regie_identifier)
        self.basket = get_object_or_404(Basket, uuid=basket_identifier, regie=self.regie, status='open')

    def get_serializer_kwargs(self, request):
        return {
            'data': request.data,
            'basket': self.basket,
        }

    def get_current_payer_id(self, serializer):
        return self.basket.payer_external_id

    def process(self, request, serializer, aggregated_lines, payer_data_cache):
        basket_line_data = serializer.validated_data.copy()
        basket_line_data.pop('user_external_id')
        basket_line_data.pop('booked_events')
        basket_line_data.pop('cancelled_events')
        invoice_data = basket_line_data.copy()
        invoice_data.pop('user_first_name')
        invoice_data.pop('user_last_name')
        invoice_data.pop('information_message')
        invoice_data.pop('cancel_information_message')
        invoice_data.pop('form_url')
        invoice_data.pop('validation_callback_url')
        invoice_data.pop('payment_callback_url')
        invoice_data.pop('credit_callback_url')
        invoice_data.pop('cancel_callback_url')
        invoice_data.pop('expiration_callback_url')
        other_payer_credits_draft = []
        basket_line = None
        current_payer_external_id = self.basket.payer_external_id
        with transaction.atomic():
            # create draft invoice for each involved payer
            for payer_external_id in sorted(aggregated_lines.keys()):
                lines = aggregated_lines[payer_external_id]
                total_amount = sum(li['unit_amount'] * li['quantity'] for li in lines)
                if payer_external_id != current_payer_external_id and total_amount >= 0:
                    # amount is positive, it will generate an invoice:
                    # for other payers, only credits are generated
                    continue
                payer_data = payer_data_cache[payer_external_id]
                if payer_external_id != current_payer_external_id:
                    # create draft invoice (future credit) for other payers
                    draft_invoice = DraftInvoice.objects.create(
                        regie=self.regie,
                        origin='api',
                        label=self.basket.draft_invoice.label,
                        payer_external_id=payer_external_id,
                        payer_first_name=payer_data['first_name'],
                        payer_last_name=payer_data['last_name'],
                        payer_address=payer_data['address'],
                        payer_email=payer_data['email'],
                        payer_phone=payer_data['phone'],
                        date_due=self.basket.draft_invoice.date_due,
                        date_payment_deadline=self.basket.draft_invoice.date_payment_deadline,
                        date_publication=self.basket.draft_invoice.date_publication,
                        **invoice_data,
                    )
                else:
                    # feed basket for current payer
                    basket_line = BasketLine.objects.create(
                        basket=self.basket,
                        user_external_id=serializer.validated_data['user_external_id'],
                        closed=True,
                        group_items=False,
                        **basket_line_data,
                    )
                    for line in lines:
                        item_data = line.copy()
                        description = item_data.pop('description')
                        item_data.pop('details')
                        BasketLineItem.objects.create(
                            line=basket_line,
                            subject=description,
                            **item_data,
                        )
                    self.basket.expiry_at = now() + datetime.timedelta(minutes=settings.BASKET_EXPIRY_DELAY)
                    self.basket.save()
                    draft_invoice = self.basket.draft_invoice
                    basket_line.total_amount = total_amount

                invoice_lines = []
                for line in lines:
                    invoice_lines.append(
                        DraftInvoiceLine(
                            user_external_id=serializer.validated_data['user_external_id'],
                            user_first_name=serializer.validated_data['user_first_name'],
                            user_last_name=serializer.validated_data['user_last_name'],
                            form_url=serializer.validated_data['form_url'],
                            invoice=draft_invoice,
                            **line,
                        )
                    )
                DraftInvoiceLine.objects.bulk_create(invoice_lines)
                if payer_external_id != current_payer_external_id:
                    # store generated credits to return them in response payload
                    other_payer_credits_draft.append(draft_invoice)
            # add generated credits to basket: all credits will be displayed to the user,
            # credits generated in this call and credit from other calls
            self.basket.other_payer_credits_draft.add(*other_payer_credits_draft)

            data = {}
            if basket_line:
                self.basket.draft_invoice.refresh_from_db()  # refresh amounts
                data.update(
                    {
                        'line_id': basket_line.uuid,
                        'closed': True,
                        'line_total_amount': basket_line.total_amount,
                        'basket_total_amount': self.basket.draft_invoice.total_amount,
                    }
                )
            if other_payer_credits_draft:
                data['other_payer_credit_draft_ids'] = [str(di.uuid) for di in other_payer_credits_draft]

        return data


basket_basket_lines_from_bookings = BasketLinesFromBookingsView.as_view()


class BasketLinesFromBookingsDryRunView(FromBookingsMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketLinesFromBookingsDryRunSerializer

    def post_init(self, request, regie_identifier):
        self.regie = get_object_or_404(Regie, slug=regie_identifier)

    def get_current_payer_id(self, serializer):
        return serializer.validated_data['payer_external_id']

    def process(self, request, serializer, aggregated_lines, payer_data_cache):
        current_payer_external_id = serializer.validated_data['payer_external_id']
        # invoicing_element for each involved payer
        current_payer_invoicing_element = None
        other_payer_credit_drafts = []
        for payer_external_id in sorted(aggregated_lines.keys()):
            lines = aggregated_lines[payer_external_id]
            payer_data = payer_data_cache[payer_external_id]
            invoicing_element_lines = []
            for line in lines:
                invoicing_element_lines.append(
                    {
                        'label': line['label'],
                        'description': line['description'],
                        'unit_amount': line['unit_amount'],
                        'quantity': line['quantity'],
                        'total_amount': line['quantity'] * line['unit_amount'],
                    }
                )
            total_amount = sum(li['total_amount'] for li in invoicing_element_lines)
            if payer_external_id != current_payer_external_id:
                if total_amount >= 0:
                    # amount is positive, it will generate an invoice:
                    # for other payers, only credits are generated
                    continue
                if total_amount < 0:
                    # it will be a credit, transform amounts, but only for other payers
                    total_amount *= -1
                    for line in invoicing_element_lines:
                        line['quantity'] *= -1
                        line['total_amount'] *= -1
            payer_name = f"{payer_data['first_name']} {payer_data['last_name']}".strip()
            invoicing_element = {
                'total_amount': total_amount,
                'lines': invoicing_element_lines,
                'payer_external_id': payer_external_id,
                'payer_name': payer_name,
            }
            if payer_external_id == current_payer_external_id:
                # current payer lines added in basket (amount can be negative)
                current_payer_invoicing_element = invoicing_element
            else:
                # store generated credits to return them in response payload
                other_payer_credit_drafts.append(invoicing_element)
        data = {}
        if current_payer_invoicing_element:
            data['basket'] = current_payer_invoicing_element
        if other_payer_credit_drafts:
            data['other_payer_credit_drafts'] = other_payer_credit_drafts
        return data


basket_basket_lines_from_bookings_dry_run = BasketLinesFromBookingsDryRunView.as_view()


class BasketLineItemsView(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.BasketLineItemSerializer

    def post(self, request, regie_identifier, basket_identifier, line_identifier):
        line = get_object_or_404(
            BasketLine,
            uuid=line_identifier,
            basket__uuid=basket_identifier,
            basket__status='open',
            basket__regie__slug=regie_identifier,
            closed=False,
        )
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        event_slug = serializer.validated_data.pop('slug', '')
        event_label = ''
        agenda_slug = ''
        if '@' in event_slug:
            agenda_slug = event_slug.split('@')[0]
            event_label = serializer.validated_data['label']
        item = BasketLineItem.objects.create(
            line=line,
            event_slug=event_slug,
            event_label=event_label,
            agenda_slug=agenda_slug,
            **serializer.validated_data,
        )

        return Response({'data': {'item_id': str(item.uuid)}})


basket_basket_line_items = BasketLineItemsView.as_view()


class BasketLineCloseView(APIView):
    permission_classes = (APIAdmin,)

    def post(self, request, regie_identifier, basket_identifier, line_identifier):
        self.line = get_object_or_404(
            BasketLine,
            uuid=line_identifier,
            basket__uuid=basket_identifier,
            basket__status='open',
            basket__regie__slug=regie_identifier,
            closed=False,
        )
        self.line.closed = True
        self.line.save()
        self.generate_invoice_lines()
        basket = self.line.basket
        basket.expiry_at = now() + datetime.timedelta(minutes=settings.BASKET_EXPIRY_DELAY)
        basket.save()

        return Response({'data': {'line_id': str(self.line.uuid), 'closed': True}})

    def generate_invoice_lines(self):
        for (
            label,
            description,
            quantity,
            unit_amount,
            dummy,
            event_date,
            event_slug,
            event_label,
            agenda_slug,
            activity_label,
            accounting_code,
            dates,
        ) in self.line.formatted_items:
            DraftInvoiceLine.objects.create(
                invoice=self.line.basket.draft_invoice,
                event_date=event_date,
                event_slug=event_slug,
                event_label=event_label,
                agenda_slug=agenda_slug,
                activity_label=activity_label,
                label=label,
                description=description,
                user_external_id=self.line.user_external_id,
                user_first_name=self.line.user_first_name,
                user_last_name=self.line.user_last_name,
                quantity=quantity,
                unit_amount=unit_amount,
                accounting_code=accounting_code,
                form_url=self.line.form_url,
                details={'dates': dates},
            )


basket_basket_line_close = BasketLineCloseView.as_view()


class BasketLineCancelView(APIView):
    permission_classes = (APIAdmin,)

    def post(self, request, regie_identifier, basket_identifier, line_identifier):
        line = get_object_or_404(
            BasketLine,
            uuid=line_identifier,
            basket__uuid=basket_identifier,
            basket__status='open',
            basket__regie__slug=regie_identifier,
            closed=False,
        )
        line.items.all().delete()
        line.delete()

        return Response({'err': 0})


basket_basket_line_cancel = BasketLineCancelView.as_view()
