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

import datetime
import decimal

import eopayment
from django.conf import settings
from django.contrib import messages
from django.db.models import OuterRef, Q, Subquery
from django.http import HttpResponseBadRequest, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from lingo.invoicing.models import Payment
from lingo.manager.utils import CanBeManagedRequiredMixin, CanBeViewedRequiredMixin, StaffRequiredMixin

from .forms import PaymentBackendCreateForm, PaymentBackendForm, TransactionFilterSet
from .models import PaymentBackend, Transaction


class BackendList(ListView):
    model = PaymentBackend
    template_name = 'lingo/epayment/backend_list.html'

    def get_queryset(self):
        if not self.request.user.is_staff:
            group_ids = [x.id for x in self.request.user.groups.all()]
            return (
                super().get_queryset().filter(Q(view_role_id__in=group_ids) | Q(edit_role_id__in=group_ids))
            )
        return super().get_queryset()


backend_list = BackendList.as_view()


class BackendCreateView(StaffRequiredMixin, CreateView):
    model = PaymentBackend
    form_class = PaymentBackendCreateForm
    template_name = 'lingo/epayment/backend_form.html'

    def get_success_url(self):
        messages.info(self.request, _('Please fill additional backend parameters.'))
        return reverse_lazy('lingo-manager-epayment-backend-detail', kwargs={'pk': self.object.pk})


backend_add = BackendCreateView.as_view()


class BackendDetailView(CanBeViewedRequiredMixin, DetailView):
    model = PaymentBackend
    template_name = 'lingo/epayment/backend_detail.html'

    def get_context_data(self, **kwargs):
        kwargs['user_can_manage'] = self.object.can_be_managed(self.request.user)
        return super().get_context_data(**kwargs)


backend_detail = BackendDetailView.as_view()


class BackendUpdateView(CanBeManagedRequiredMixin, UpdateView):
    model = PaymentBackend
    form_class = PaymentBackendForm
    success_url = reverse_lazy('lingo-manager-epayment-backend-list')
    template_name = 'lingo/epayment/backend_form.html'

    def get_success_url(self):
        return reverse_lazy('lingo-manager-epayment-backend-detail', kwargs={'pk': self.object.pk})


backend_edit = BackendUpdateView.as_view()


class BackendDeleteView(CanBeManagedRequiredMixin, DeleteView):
    model = PaymentBackend
    success_url = reverse_lazy('lingo-manager-epayment-backend-list')
    template_name = 'lingo/epayment/backend_confirm_delete.html'


backend_delete = BackendDeleteView.as_view()


class TransactionListView(ListView):
    model = Transaction
    template_name = 'lingo/epayment/transaction_list.html'
    paginate_by = 100

    def get_queryset(self):
        queryset = super().get_queryset().select_related('invoice')
        payment_queryset = Payment.objects.filter(order_id=OuterRef('order_id')).order_by('pk')
        queryset = queryset.annotate(
            payment_formatted_number=Subquery(payment_queryset.values('formatted_number')[:1])
        )
        self.backend_exists = True
        if not self.request.user.is_staff:
            group_ids = [x.id for x in self.request.user.groups.all()]
            backend_qs = PaymentBackend.objects.filter(
                Q(view_role_id__in=group_ids) | Q(edit_role_id__in=group_ids)
            )
            self.backend_exists = backend_qs.exists()
            queryset = queryset.filter(backend__in=backend_qs)
        self.filterset = TransactionFilterSet(
            data=self.request.GET or None,
            queryset=queryset.order_by('-start_date'),
        )
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['filterset'] = self.filterset
        kwargs['backend_exists'] = self.backend_exists
        return super().get_context_data(**kwargs)


transaction_list = TransactionListView.as_view()


def pay(request, *, transaction):
    next_url = request.GET.get('next_url') or '/'
    eopayment_kwargs = {
        'merchant_name': settings.TEMPLATE_VARS.get('global_title') or 'Compte Citoyen',
    }
    user = request.user if request.user.is_authenticated else None
    if user:
        transaction.user = user
        eopayment_kwargs = {'email': user.email, 'first_name': user.first_name, 'last_name': user.last_name}

    error = transaction.backend.check_payment_is_possible(transaction.amount)
    if not error and transaction.backend.eopayment.is_email_required and not eopayment_kwargs.get('email'):
        error = _('The payment requires an email address.')

    if error:
        return TemplateResponse(
            request, 'lingo/epayment/payment_message.html', {'next_url': next_url, 'error': error}
        )

    try:
        (order_id, kind, data) = transaction.backend.make_eopayment(
            request=request, transaction_id=transaction.id
        ).request(transaction.amount, **eopayment_kwargs)
    except eopayment.PaymentException as e:
        error = _('Unexpected error: %s') % str(e)
        return TemplateResponse(
            request, 'lingo/epayment/payment_message.html', {'next_url': next_url, 'error': error}
        )

    transaction.order_id = order_id
    transaction.save()
    if kind == eopayment.URL:
        return HttpResponseRedirect(data)
    else:
        return TemplateResponse(request, 'lingo/epayment/payment_redirect_form.html', {'form': data})


def pay_demo(request):
    if not settings.DEBUG:
        return HttpResponseNotFound()
    if 'backend' in request.GET:
        backend = PaymentBackend.objects.get(slug=request.GET['backend'])
    else:
        backend = PaymentBackend.objects.all().first()
    transaction = Transaction.objects.create(
        status=0,
        amount=decimal.Decimal(request.GET.get('amount', '20')),
        backend=backend,
        next_url=request.GET.get('next_url'),
    )
    return pay(request, transaction=transaction)


def pay_invoice(request, regie, invoice, amount, next_url='/'):
    if amount <= 0:
        return HttpResponseBadRequest('negative or null amount')
    backend = get_object_or_404(PaymentBackend, regie=regie)
    transaction = Transaction.objects.create(
        status=0,
        invoice=invoice,
        amount=amount,
        backend=backend,
        next_url=request.GET.get('next_url') or next_url,
    )
    return pay(request, transaction=transaction)


def pay_invoice_view(request, invoice_uuid):
    from lingo.invoicing.models import Invoice

    invoice = get_object_or_404(Invoice, uuid=invoice_uuid)
    error = None
    if invoice.remaining_amount <= 0:
        error = _('This invoice has already been paid.')
    elif invoice.cancelled_at:
        error = _('This invoice has been cancelled.')
    elif invoice.collection:
        error = _('This invoice has been collected.')
    elif invoice.basket_set.exists():
        error = _('This invoice must be paid using the basket.')
    elif invoice.pool and not invoice.pool.campaign.finalized:
        error = _('This invoice cannot yet be paid.')

    if error:
        next_url = request.GET.get('next_url') or '/'
        return TemplateResponse(
            request, 'lingo/epayment/payment_message.html', {'next_url': next_url, 'error': error}
        )

    return pay_invoice(request, invoice.regie, invoice, invoice.remaining_amount)


def handle_backend_message(request, transaction_id=None, redirect=False):
    query_string = request.environ['QUERY_STRING']
    body = force_str(request.body)
    if transaction_id:
        transaction = Transaction.objects.get(id=transaction_id)
    else:
        _, order_id = eopayment.Payment.guess(
            method=request.method,
            query_string=query_string,
            body=body,
        )
        transaction = Transaction.objects.get(order_id=order_id)
    if transaction.is_running():
        payment_response = transaction.backend.eopayment.response(
            body if request.method == 'POST' and body else query_string,
            order_id_hint=transaction.order_id,
            order_status_hint=transaction.status,
            redirect=redirect,
        )
        transaction.handle_backend_response(payment_response)
    return transaction


@csrf_exempt
def pay_callback(request, transaction_id=None):
    try:
        handle_backend_message(request, transaction_id)
    except eopayment.BackendNotFound:
        return JsonResponse({'err': 1})
    return JsonResponse({'err': 0})


@csrf_exempt
def pay_return(request, transaction_id=None):
    try:
        transaction = handle_backend_message(request, transaction_id, redirect=True)
    except eopayment.BackendNotFound:
        return HttpResponseBadRequest('invalid parameters, no payment backend')
    return HttpResponseRedirect(
        reverse_lazy('lingo-epayment-processing', kwargs={'transaction_id': transaction.id})
    )


class PaymentProcessing(TemplateView):
    template_name = 'lingo/epayment/payment_processing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        transaction = get_object_or_404(Transaction, id=self.kwargs.get('transaction_id'))
        transaction.check_status()
        context['short_wait'] = bool(
            transaction.end_date and (now() - transaction.end_date) > datetime.timedelta(seconds=300)
        )
        context['next_url'] = transaction.next_url or '/'
        context['processing_status_url'] = reverse_lazy(
            'lingo-epayment-processing-status', kwargs=self.kwargs
        )
        return context


payment_processing = PaymentProcessing.as_view()


def payment_processing_status(request, transaction_id):
    transaction = Transaction.objects.get(id=transaction_id)
    transaction.check_status()
    return JsonResponse(
        {
            'status': transaction.status,
            'running': transaction.is_running(),
            'paid': transaction.is_paid(),
        }
    )
