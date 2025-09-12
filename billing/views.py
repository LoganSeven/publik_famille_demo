# billing/views.py
"""
Views for the billing application.

This module defines the payment flow for invoices, including
access control, payment processing through gateways, PDF
generation, and document storage.
"""

import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone  # noqa: F401  # May be used in extensions
from django.conf import settings

from .models import Invoice
from .exceptions import (
    BillingError,
    PaymentError,
    PDFGenerationError,
    DocumentStorageError,
)
from .pdf import generate_invoice_pdf
from .gateways import get_billing_gateway
from documents.models import Document, DocumentKind
from monitoring.html_logger import info, warn, error


@login_required
@require_POST
def pay_invoice(request, pk):
    """
    Handle invoice payment.

    Verifies user access, processes payment via the billing
    gateway, generates a PDF copy of the invoice, and stores
    it as a Document. Returns appropriate messages for the user
    in case of success, duplicate payment, or failure.

    Parameters
    ----------
    request : HttpRequest
        The current HTTP request.
    pk : int
        Primary key of the invoice to be paid.

    Returns
    -------
    HttpResponseRedirect
        A redirect to the enrollments list page, with messages
        describing the result of the operation.
    """
    invoice = get_object_or_404(Invoice, pk=pk)

    # --- Access control ---
    if invoice.enrollment.child.parent_id != request.user.id:
        messages.error(request, "Access denied.")
        error(
            f"Unauthorized payment attempt invoice={invoice.pk} "
            f"by user={request.user.id}."
        )
        return redirect("activities:enrollments")

    # --- Duplicate payment check ---
    if invoice.status == Invoice.Status.PAID:
        messages.info(request, "This invoice is already paid.")
        info(f"Invoice already paid invoice={invoice.pk}.")
        return redirect("activities:enrollments")

    try:
        # --- Mark invoice as paid through gateway ---
        gw = get_billing_gateway()
        gw.mark_paid(invoice)
        info(f"Payment accepted invoice={invoice.pk}.")

        # --- Generate invoice PDF ---
        try:
            rel_dir = os.path.join("invoices")
            os.makedirs(os.path.join(settings.MEDIA_ROOT, rel_dir), exist_ok=True)
            rel_path = os.path.join(rel_dir, f"invoice_{invoice.pk}.pdf")
            full_path = os.path.join(settings.MEDIA_ROOT, rel_path)
            generate_invoice_pdf(invoice, full_path)
        except Exception as ex:
            error(f"PDF generation error invoice={invoice.pk}: {ex}")
            raise PDFGenerationError(
                "Invoice was paid but PDF generation failed."
            )

        # --- Store document in the Document model ---
        try:
            doc = Document.objects.create(
                user=invoice.enrollment.child.parent,
                kind=DocumentKind.FACTURE,
                title=f"Invoice #{invoice.pk}",
                file=rel_path,
                invoice=invoice,
            )
            info(f"Document created id={doc.id} for invoice={invoice.pk}.")
        except Exception as ex:
            error(f"Document storage error invoice={invoice.pk}: {ex}")
            raise DocumentStorageError(
                "PDF was generated but could not be stored as a document."
            )

        messages.success(
            request,
            "Payment completed. Invoice available in My Documents > Invoices.",
        )
        return redirect("activities:enrollments")

    except (BillingError, PaymentError) as ex:
        # --- Handle billing or payment errors ---
        warn(f"Post-payment incident invoice={invoice.pk}: {ex}")
        messages.warning(request, str(ex))
        return redirect("activities:enrollments")
