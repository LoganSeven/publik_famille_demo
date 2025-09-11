# billing/models.py

from django.db import models
from django.utils import timezone
from activities.models import Enrollment


class Invoice(models.Model):
    """
    Invoice bound 1:1 to an Enrollment.

    Notes:
    - `lingo_id` stores the remote identifier when the Lingo backend is used.
    - Default status is UNPAID; payment sets PAID and `paid_on`.
    """
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Non payée'
        PAID = 'PAID', 'Payée'

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.CASCADE,
        related_name='invoice',
        verbose_name='Inscription',
    )
    amount = models.DecimalField('Montant (€)', max_digits=8, decimal_places=2, default=0)
    status = models.CharField('Statut', max_length=16, choices=Status.choices, default=Status.UNPAID)
    issued_on = models.DateTimeField('Émise le', default=timezone.now)
    paid_on = models.DateTimeField('Payée le', null=True, blank=True)

    # Remote provider identifier (Lingo)
    lingo_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ['-issued_on']

    def __str__(self) -> str:
        return f"Facture #{self.pk} - {self.enrollment} - {self.amount}€ ({self.status})"


# ---------------------------------------------------------------------------
# Lazy access shim for Enrollment.invoice
# ---------------------------------------------------------------------------
def _lazy_invoice(enroll: Enrollment) -> Invoice:
    """
    Return the invoice associated to an enrollment, creating it on demand
    if it does not exist yet. The created invoice uses the activity fee as amount.

    This keeps tests and templates safe when accessing `enroll.invoice` directly.
    """
    # Local import to avoid circular import when Django loads apps
    from .models import Invoice as _Invoice

    invoice, _ = _Invoice.objects.get_or_create(
        enrollment=enroll,
        defaults={'amount': enroll.activity.fee},
    )
    return invoice


# Replace the Django reverse-relation descriptor by a Python-level property.
# This is intentionally done to avoid RelatedObjectDoesNotExist when test
# suites (or templates) access `enroll.invoice` before a row exists.
Enrollment.invoice = property(_lazy_invoice)  # type: ignore[attr-defined]
