# billing/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.conf import settings
import os

from .models import Invoice
from .exceptions import BillingError, PaymentError, PDFGenerationError, DocumentStorageError
from .pdf import generate_invoice_pdf
from .gateways import get_billing_gateway
from documents.models import Document, DocumentKind
from monitoring.html_logger import info, warn, error

@login_required
@require_POST
def pay_invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    # Vérification d'accès
    if invoice.enrollment.child.parent_id != request.user.id:
        messages.error(request, "Accès refusé.")
        error(f"Tentative de paiement non autorisée invoice={invoice.pk} by user={request.user.id}.")
        return redirect('activities:enrollments')

    if invoice.status == Invoice.Status.PAID:
        messages.info(request, "Cette facture est déjà payée.")
        info(f"Facture déjà payée invoice={invoice.pk}.")
        return redirect('activities:enrollments')

    try:
        # Marquer payé
        invoice.status = Invoice.Status.PAID
        invoice.paid_on = timezone.now()
        invoice.save(update_fields=['status', 'paid_on'])
        info(f"Paiement accepté invoice={invoice.pk}.")

        # Génération du PDF & Document
        try:
            rel_dir = os.path.join('invoices')
            os.makedirs(os.path.join(settings.MEDIA_ROOT, rel_dir), exist_ok=True)
            rel_path = os.path.join(rel_dir, f'invoice_{invoice.pk}.pdf')
            full_path = os.path.join(settings.MEDIA_ROOT, rel_path)
            generate_invoice_pdf(invoice, full_path)
        except Exception as ex:
            error(f"Erreur génération PDF invoice={invoice.pk}: {{ex}}")
            raise PDFGenerationError("La facture a été payée mais la génération du PDF a échoué.")

        try:
            doc = Document.objects.create(
                user=invoice.enrollment.child.parent,
                kind=DocumentKind.FACTURE,
                title=f"Facture #{invoice.pk}",
                file=rel_path,
                invoice=invoice
            )
            info(f"Document créé id={doc.id} pour invoice={invoice.pk}.")
        except Exception as ex:
            error(f"Erreur enregistrement Document invoice={invoice.pk}: {{ex}}")
            raise DocumentStorageError("Le PDF a été généré mais n'a pas pu être enregistré.")

        messages.success(request, "Paiement effectué. Facture disponible dans Mes documents > Factures.")
        return redirect('activities:enrollments')

    except BillingError as ex:
        # Paiement ok mais incident de génération/enregistrement
        warn(f"Incident post-paiement invoice={invoice.pk}: {{ex}}")
        messages.warning(request, str(ex))
        return redirect('activities:enrollments')
