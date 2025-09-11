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
    """
    Abstraction for billing backends. Implementations must be idempotent regarding
    invoice creation for a given Enrollment.
    """
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice: ...
    def mark_paid(self, invoice: Invoice) -> Invoice: ...


# ---------------------------------------------------------------------------
# Local (pure-Django) billing backend
# ---------------------------------------------------------------------------
@dataclass
class LocalBillingGateway:
    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        """
        Create or fetch the invoice for this enrollment. Idempotent.
        """
        invoice, created = Invoice.objects.get_or_create(
            enrollment=enrollment,
            defaults={'amount': amount},
        )
        if not created and invoice.amount != amount:
            invoice.amount = amount
            invoice.save(update_fields=['amount'])
        return invoice

    def mark_paid(self, invoice: Invoice) -> Invoice:
        """
        Mark invoice as paid locally and confirm the enrollment.
        """
        if invoice.status == Invoice.Status.PAID:
            return invoice

        invoice.status = Invoice.Status.PAID
        invoice.paid_on = timezone.now()
        invoice.save(update_fields=['status', 'paid_on'])

        # Also confirm the related enrollment
        enroll = invoice.enrollment
        if enroll.status != Enrollment.Status.CONFIRMED:
            enroll.status = Enrollment.Status.CONFIRMED
            enroll.save(update_fields=['status'])

        return invoice


# ---------------------------------------------------------------------------
# Lingo-backed billing backend (remote HTTP calls)
# ---------------------------------------------------------------------------
@dataclass
class LingoGateway:
    base_url: str | None = None  # e.g. http://localhost:8080

    def _require_base(self) -> str:
        base = self.base_url or os.getenv('BILLING_LINGO_BASE_URL')
        if not base:
            raise BillingError("BILLING_LINGO_BASE_URL is not configured")
        return base.rstrip('/')

    def create_invoice(self, enrollment: Enrollment, amount) -> Invoice:
        """
        Create (or fetch) the local invoice while mirroring creation to Lingo.

        The call is idempotent on the local DB: if an invoice already exists for
        the enrollment, it is updated with the latest amount/lingo_id.
        """
        base = self._require_base()
        url = f"{base}/invoices"
        payload = {"amount": float(Decimal(str(amount)))}

        try:
            resp = requests.post(url, json=payload, timeout=5)
            resp.raise_for_status()
        except RequestException as exc:
            logger.exception("Lingo create_invoice failed")
            raise BillingError("Failed to create invoice at Lingo") from exc

        data = resp.json()
        lingo_id = data.get("id")

        invoice, created = Invoice.objects.get_or_create(
            enrollment=enrollment,
            defaults={'amount': amount, 'lingo_id': lingo_id},
        )
        if not created:
            changed_fields: list[str] = []
            if invoice.amount != amount:
                invoice.amount = amount
                changed_fields.append('amount')
            if lingo_id and invoice.lingo_id != lingo_id:
                invoice.lingo_id = lingo_id
                changed_fields.append('lingo_id')
            if changed_fields:
                invoice.save(update_fields=changed_fields)

        return invoice

    def mark_paid(self, invoice: Invoice) -> Invoice:
        """
        Ask Lingo to mark the remote invoice as paid, then update our local record
        and confirm the related enrollment.
        """
        if invoice.status == Invoice.Status.PAID:
            # Still ensure enrollment is confirmed
            enroll = invoice.enrollment
            if enroll.status != Enrollment.Status.CONFIRMED:
                enroll.status = Enrollment.Status.CONFIRMED
                enroll.save(update_fields=['status'])
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

        # Confirm the related enrollment locally
        enroll = invoice.enrollment
        if enroll.status != Enrollment.Status.CONFIRMED:
            enroll.status = Enrollment.Status.CONFIRMED
            enroll.save(update_fields=['status'])

        return invoice


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_billing_gateway() -> BillingGateway:
    from django.conf import settings
    backend = getattr(settings, 'BILLING_BACKEND', 'local')
    if backend == 'lingo':
        return LingoGateway(base_url=getattr(settings, 'BILLING_LINGO_BASE_URL', None))
    return LocalBillingGateway()
