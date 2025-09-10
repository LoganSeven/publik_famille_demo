# billing/gateways.py
from dataclasses import dataclass
from typing import Protocol
from django.utils import timezone
from .models import Invoice
from activities.models import Enrollment

class BillingGateway(Protocol):
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice: ...
    def mark_paid(self, invoice: Invoice) -> Invoice: ...

@dataclass
class LocalBillingGateway:
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        return Invoice.objects.create(enrollment=enrollment, amount=amount)

    def mark_paid(self, invoice: Invoice) -> Invoice:
        if invoice.status == Invoice.Status.PAID:
            return invoice
        invoice.status = Invoice.Status.PAID
        invoice.paid_on = timezone.now()
        invoice.save(update_fields=['status', 'paid_on'])
        return invoice

@dataclass
class LingoGateway:
    base_url: str | None = None

    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        # Simulation locale: on crée une ligne Invoice, en conditions réelles on appellerait Lingo (API) et stockerait un id externe.
        return Invoice.objects.create(enrollment=enrollment, amount=amount)

    def mark_paid(self, invoice: Invoice) -> Invoice:
        # Simulation locale: on "reçoit" un retour OK de Lingo et on marque payé.
        if invoice.status == Invoice.Status.PAID:
            return invoice
        invoice.status = Invoice.Status.PAID
        invoice.paid_on = timezone.now()
        invoice.save(update_fields=['status', 'paid_on'])
        return invoice

def get_billing_gateway():
    from django.conf import settings
    backend = getattr(settings, 'BILLING_BACKEND', 'local')
    if backend == 'lingo':
        return LingoGateway(base_url=getattr(settings, 'BILLING_LINGO_BASE_URL', None))
    return LocalBillingGateway()
