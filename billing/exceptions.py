# billing/exceptions.py
class BillingError(Exception):
    """Erreur générique de facturation."""

class PaymentError(BillingError):
    """Erreur lors du paiement."""

class PDFGenerationError(BillingError):
    """Erreur lors de la génération de la facture PDF."""

class DocumentStorageError(BillingError):
    """Erreur lors de l'enregistrement du document lié à la facture."""
