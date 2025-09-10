# billing/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Invoice
from activities.models import Enrollment

@receiver(post_save, sender=Invoice)
def mark_enrollment_confirmed_on_paid(sender, instance: Invoice, **kwargs):
    if instance.status == Invoice.Status.PAID:
        enrollment = instance.enrollment
        if enrollment.status != Enrollment.Status.CONFIRMED:
            enrollment.status = Enrollment.Status.CONFIRMED
            enrollment.approved_on = None
            enrollment.save(update_fields=['status', 'approved_on'])
