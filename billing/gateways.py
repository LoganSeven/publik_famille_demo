# billing/gateways.py
from dataclasses import dataclass
from typing import Protocol
import logging
import os
from decimal import Decimal
import requests
from requests.exceptions import RequestException
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Invoice
from .exceptions import BillingError, PaymentError
from activities.models import Enrollment

logger = logging.getLogger(__name__)

class BillingGateway(Protocol):
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice: ...
    def mark_paid(self, invoice: Invoice) -> Invoice: ...

@dataclass
class LocalBillingGateway:
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        invoice, created = Invoice.objects.get_or_create(
            enrollment=enrollment,
            defaults={"amount": amount},
        )
        if not created and invoice.amount != amount:
            invoice.amount = amount
            invoice.save(update_fields=["amount"])
        return invoice

    def mark_paid(self, invoice: Invoice) -> Invoice:
        if invoice.status == Invoice.Status.PAID:
            return invoice
        invoice.status = Invoice.Status.PAID
        invoice.paid_on = timezone.now()
        invoice.save(update_fields=['status', 'paid_on'])
        return invoice

@dataclass
class LingoGateway:
    base_url: str | None = None  # e.g. http://localhost:8080

    def _require_base(self) -> str:
        base = self.base_url or os.getenv('BILLING_LINGO_BASE_URL')
        if not base:
            raise BillingError("BILLING_LINGO_BASE_URL is not configured")
        return base.rstrip('/')

    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        base = self._require_base()
        url = f"{base}/invoices"
        payload = {"amount": float(Decimal(str(amount)))}
        # … requête HTTP pour créer la facture côté Lingo …
        data = resp.json()
        lingo_id = data.get("id")
        invoice, created = Invoice.objects.get_or_create(
            enrollment=enrollment,
            defaults={"amount": amount, "lingo_id": lingo_id},
        )
        if not created:
            invoice.amount = amount
            invoice.lingo_id = lingo_id
            invoice.save(update_fields=["amount", "lingo_id"])
        return invoice


    def mark_paid(self, invoice: Invoice) -> Invoice:
        if invoice.status == Invoice.Status.PAID:
            return invoice
        base = self._require_base()
        if not invoice.lingo_id:
            raise PaymentError("Invoice has no lingo_id")
        url = f"{base}/invoices/{invoice.lingo_id}/pay"
        try:
            resp = requests.post(url, timeout=5)
            resp.raise_for_status()
        except RequestException as exc:
            logger.exception("Lingo mark_paid failed")
            raise PaymentError("Failed to mark invoice paid at Lingo") from exc
        data = resp.json()
        new_status = data.get("status") or Invoice.Status.PAID
        invoice.status = new_status
        paid_str = data.get("paid_on")
        dt = parse_datetime(paid_str) if paid_str else timezone.now()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        invoice.paid_on = dt
        invoice.save(update_fields=['status', 'paid_on'])
        return invoice

def get_billing_gateway() -> BillingGateway:
    from django.conf import settings
    backend = getattr(settings, 'BILLING_BACKEND', 'local')
    if backend == 'lingo':
        return LingoGateway(base_url=getattr(settings, 'BILLING_LINGO_BASE_URL', None))
    return LocalBillingGateway()
