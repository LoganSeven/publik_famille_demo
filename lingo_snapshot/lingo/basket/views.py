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

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.timezone import now
from django.views.generic import FormView, TemplateView

from lingo.basket.models import Basket
from lingo.epayment.views import pay_invoice
from lingo.invoicing.models import Invoice


class BasketDetailView(LoginRequiredMixin, TemplateView):
    def get_template_names(self):
        if self.basket and self.basket.status == 'tobepaid':
            return ['lingo/basket/basket_payment_in_progress.html']
        return ['lingo/basket/basket_detail.html']

    def get(self, request, *args, **kwargs):
        back_url = request.GET.get('back_url')
        if back_url:
            request.session['back_url'] = back_url
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        nameid = self.request.user.get_name_id()
        self.basket = Basket.objects.filter(
            status__in=['open', 'tobepaid'],
            payer_nameid=nameid,
        ).first()
        kwargs['basket'] = self.basket
        return super().get_context_data(**kwargs)


basket_detail = BasketDetailView.as_view()


class BasketValidateView(LoginRequiredMixin, FormView):
    template_name = 'lingo/basket/basket_validate.html'

    def dispatch(self, request, *args, **kwargs):
        if self.request.user.is_authenticated:
            nameid = self.request.user.get_name_id()
            self.basket = get_object_or_404(
                Basket,
                status='open',
                payer_nameid=nameid,
            )
            if not self.basket.lines:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['basket'] = self.basket
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if self.basket.expiry_at <= now():
            # do nothing
            return redirect(reverse('lingo-basket-detail'))

        final_object = self.basket.draft_invoice.promote()
        if isinstance(final_object, Invoice):
            self.set_invoice(final_object)
            if self.basket.remaining_amount > 0:
                return pay_invoice(
                    request=request,
                    regie=self.basket.regie,
                    invoice=self.basket.invoice,
                    amount=self.basket.remaining_amount,
                    next_url=reverse('lingo-basket-detail'),
                )
            if self.basket.total_amount > 0:
                return redirect('%s?ret=ip' % reverse('lingo-basket-confirmation'))
            return redirect('%s?ret=i' % reverse('lingo-basket-confirmation'))

        self.set_credit(final_object)
        return redirect('%s?ret=c' % reverse('lingo-basket-confirmation'))

    def set_invoice(self, invoice):
        self.basket.invoice = invoice
        self.basket.invoice.refresh_from_db()  # refresh amounts
        payment = self.basket.assign_credits()

        if self.basket.remaining_amount == 0:
            self.basket.status = 'completed'
            self.basket.validated_at = now()
            self.basket.paid_at = now()
            self.basket.completed_at = now()
            self.basket.save()
            self.basket.notify(
                payload=invoice.get_notification_payload(self.request),
                notification_type='validation',
            )
            self.basket.notify(
                payload=payment.get_notification_payload(self.request) if payment else None,
                notification_type='payment',
            )
        else:
            self.basket.status = 'tobepaid'
            self.basket.validated_at = now()
            self.basket.save()
            self.basket.notify(
                payload=invoice.get_notification_payload(self.request),
                notification_type='validation',
            )

    def set_credit(self, credit):
        credit.make_assignments()
        self.basket.credit = credit
        self.basket.credit.refresh_from_db()  # refresh amounts
        self.basket.status = 'completed'
        self.basket.validated_at = now()
        self.basket.paid_at = now()
        self.basket.completed_at = now()
        self.basket.save()
        self.basket.notify(
            payload=credit.get_notification_payload(self.request), notification_type='validation'
        )
        self.basket.notify(payload=credit.get_notification_payload(self.request), notification_type='credit')


basket_validate = BasketValidateView.as_view()


class BasketCancelView(LoginRequiredMixin, FormView):
    template_name = 'lingo/basket/basket_cancel.html'

    def dispatch(self, request, *args, **kwargs):
        if self.request.user.is_authenticated:
            nameid = self.request.user.get_name_id()
            self.basket = get_object_or_404(
                Basket,
                status__in=['open', 'tobepaid'],
                payer_nameid=nameid,
            )
            if not self.basket.lines:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['basket'] = self.basket
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        self.basket.cancel(user=request.user)
        return redirect(reverse('lingo-basket-detail'))


basket_cancel = BasketCancelView.as_view()


class BasketConfirmationView(LoginRequiredMixin, TemplateView):
    template_name = 'lingo/basket/basket_confirmation.html'

    def get_context_data(self, **kwargs):
        kwargs['invoice'] = self.request.GET.get('ret') == 'i'
        kwargs['invoice_paid'] = self.request.GET.get('ret') == 'ip'
        kwargs['credit'] = self.request.GET.get('ret') == 'c'
        return super().get_context_data(**kwargs)


basket_confirmation = BasketConfirmationView.as_view()


class BasketStatusJsView(LoginRequiredMixin, TemplateView):
    template_name = 'lingo/basket/basket_status.js'
    content_type = 'application/javascript'

    def get_context_data(self, **kwargs):
        nameid = self.request.user.get_name_id()
        basket = Basket.objects.filter(
            status__in=['open', 'tobepaid'],
            payer_nameid=nameid,
            expiry_at__gte=now(),
        ).first()
        kwargs['basket'] = basket
        return super().get_context_data(**kwargs)


basket_status_js = BasketStatusJsView.as_view()
