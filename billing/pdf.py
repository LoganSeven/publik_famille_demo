# billing/pdf.py
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from django.utils.timezone import now

def generate_invoice_pdf(invoice, file_path: str):
    """Génère un PDF de facture très simple en euros."""
    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin

    # En-tête
    c.setFont('Helvetica-Bold', 16)
    c.drawString(margin, y, f"FACTURE #{invoice.pk}")
    y -= 12*mm
    c.setFont('Helvetica', 11)
    c.drawString(margin, y, f"Date: {now().strftime('%Y-%m-%d %H:%M')}")
    y -= 8*mm
    c.drawString(margin, y, "Émetteur: Publik Famille Demo")
    y -= 8*mm

    # Destinataire
    parent = invoice.enrollment.child.parent
    child = invoice.enrollment.child
    activity = invoice.enrollment.activity
    c.drawString(margin, y, f"Destinataire: {parent.username} ({parent.email or 'n/a'})")
    y -= 8*mm
    c.drawString(margin, y, f"Enfant: {child.first_name} {child.last_name}")
    y -= 8*mm

    # Détail
    c.setFont('Helvetica-Bold', 12)
    c.drawString(margin, y, "Détail:")
    y -= 8*mm
    c.setFont('Helvetica', 11)
    c.drawString(margin, y, f"Activité: {activity.title}")
    y -= 8*mm
    c.drawString(margin, y, f"Montant: {invoice.amount} €")
    y -= 12*mm

    # Pied de page
    c.setFont('Helvetica-Oblique', 10)
    c.drawString(margin, 15*mm, "Merci pour votre règlement.")
    c.showPage()
    c.save()
