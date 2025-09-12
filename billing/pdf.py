# billing/pdf.py
"""
PDF generation utilities for the billing application.

This module provides functionality to generate PDF invoices
using ReportLab. The generated document includes basic
information such as invoice ID, date, payer details,
activity, and amount.
"""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from django.utils.timezone import now


def generate_invoice_pdf(invoice, file_path: str):
    """
    Generate a simple invoice PDF in euros.

    Parameters
    ----------
    invoice : Invoice
        The invoice instance for which the PDF is generated.
    file_path : str
        The full file system path where the PDF will be saved.

    Notes
    -----
    - Uses ReportLab for PDF generation.
    - Layout includes header, recipient details, activity,
      amount, and a footer.
    - The invoice file is saved to the specified location.
    """
    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin

    # --- Header section ---
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, f"FACTURE #{invoice.pk}")
    y -= 12 * mm
    c.setFont("Helvetica", 11)
    c.drawString(margin, y, f"Date: {now().strftime('%Y-%m-%d %H:%M')}")
    y -= 8 * mm
    c.drawString(margin, y, "Émetteur: Publik Famille Demo")
    y -= 8 * mm

    # --- Recipient details ---
    parent = invoice.enrollment.child.parent
    child = invoice.enrollment.child
    activity = invoice.enrollment.activity
    c.drawString(
        margin,
        y,
        f"Destinataire: {parent.username} ({parent.email or 'n/a'})",
    )
    y -= 8 * mm
    c.drawString(margin, y, f"Enfant: {child.first_name} {child.last_name}")
    y -= 8 * mm

    # --- Activity and amount details ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Détail:")
    y -= 8 * mm
    c.setFont("Helvetica", 11)
    c.drawString(margin, y, f"Activité: {activity.title}")
    y -= 8 * mm
    c.drawString(margin, y, f"Montant: {invoice.amount} €")
    y -= 12 * mm

    # --- Footer ---
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(margin, 15 * mm, "Merci pour votre règlement.")
    c.showPage()
    c.save()
