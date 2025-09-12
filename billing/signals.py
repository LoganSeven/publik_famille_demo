# billing/signals.py
"""
Signals for the billing application.

This module defines signal handlers for keeping billing
and enrollment states synchronized. In particular, it
updates the enrollment status when an invoice is paid.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Invoice
from activities.models import Enrollment


@receiver(post_save, sender=Invoice)
def mark_enrollment_confirmed_on_paid(sender, instance: Invoice, **kwargs):
    """
    Update enrollment status when an invoice is marked as paid.

    Parameters
    ----------
    sender : Model
        The model class sending the signal (Invoice).
    instance : Invoice
        The invoice instance that was saved.
    **kwargs : dict
        Additional arguments provided by the signal.

    Notes
    -----
    - Triggered whenever an Invoice is saved.
    - If the invoice status is ``PAID``, the related enrollment
      is set to ``CONFIRMED``.
    - The ``approved_on`` field is reset to ``None`` when the
      status is updated.
    """
    if instance.status == Invoice.Status.PAID:
        enrollment = instance.enrollment
        if enrollment.status != Enrollment.Status.CONFIRMED:
            enrollment.status = Enrollment.Status.CONFIRMED
            enrollment.approved_on = None
            enrollment.save(update_fields=["status", "approved_on"])
