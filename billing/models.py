# billing/models.py
"""
Database models for the billing application.

This module defines the Invoice model, which is bound 1:1
to an Enrollment, and provides a lazy accessor to ensure
safe invoice retrieval or creation.
"""

from django.db import models
from django.utils import timezone
from activities.models import Enrollment


class Invoice(models.Model):
    """
    Invoice bound one-to-one with an Enrollment.

    Attributes
    ----------
    enrollment : OneToOneField
        The enrollment linked to this invoice. Cascade delete
        ensures invoices are removed when enrollments are deleted.
    amount : DecimalField
        The billed amount in euros. Defaults to zero.
    status : CharField
        Current status of the invoice. Either UNPAID or PAID.
    issued_on : DateTimeField
        Date and time when the invoice was issued. Defaults to now.
    paid_on : DateTimeField
        Date and time when the invoice was paid. Optional.
    lingo_id : CharField
        Identifier from the remote Lingo backend if applicable.
    """

    class Status(models.TextChoices):
        """
        Enumeration of invoice statuses.

        UNPAID
            Invoice has been issued but not yet paid.
        PAID
            Invoice has been paid.
        """

        UNPAID = "UNPAID", "Non payée"
        PAID = "PAID", "Payée"

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="invoice",
        verbose_name="Inscription",
    )
    amount = models.DecimalField(
        "Montant (€)", max_digits=8, decimal_places=2, default=0
    )
    status = models.CharField(
        "Statut",
        max_length=16,
        choices=Status.choices,
        default=Status.UNPAID,
    )
    issued_on = models.DateTimeField("Émise le", default=timezone.now)
    paid_on = models.DateTimeField("Payée le", null=True, blank=True)

    # Remote provider identifier (Lingo backend)
    lingo_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        """
        Metadata for the Invoice model.

        Attributes
        ----------
        ordering : list
            Default ordering by most recent issued date.
        """

        ordering = ["-issued_on"]

    def __str__(self) -> str:
        """
        Return a string representation of the invoice.

        Returns
        -------
        str
            A formatted string including invoice ID, enrollment,
            amount, and status.
        """
        return f"Facture #{self.pk} - {self.enrollment} - {self.amount}€ ({self.status})"


# ---------------------------------------------------------------------------
# Lazy access shim for Enrollment.invoice
# ---------------------------------------------------------------------------

def _lazy_invoice(enroll: Enrollment) -> Invoice:
    """
    Return the invoice associated with an enrollment, creating it on demand.

    Parameters
    ----------
    enroll : Enrollment
        The enrollment instance for which the invoice is required.

    Returns
    -------
    Invoice
        The invoice instance, newly created if it did not exist.

    Notes
    -----
    - Uses ``activity.fee`` as the default amount when creating
      the invoice.
    - This function ensures that templates and tests accessing
      ``enroll.invoice`` will never raise RelatedObjectDoesNotExist.
    """
    # Local import avoids circular import issues when Django loads apps
    from .models import Invoice as _Invoice

    invoice, _ = _Invoice.objects.get_or_create(
        enrollment=enroll,
        defaults={"amount": enroll.activity.fee},
    )
    return invoice


# Override the Django reverse-relation descriptor with a Python property.
# This prevents RelatedObjectDoesNotExist errors when accessing
# `enroll.invoice` before an invoice has been created.
Enrollment.invoice = property(_lazy_invoice)  # type: ignore[attr-defined]
