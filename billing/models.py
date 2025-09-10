# billing/models.py
from django.db import models
from django.utils import timezone
from activities.models import Enrollment

class Invoice(models.Model):
    class Status(models.TextChoices):
        UNPAID = 'UNPAID', 'Non payée'
        PAID = 'PAID', 'Payée'

    enrollment = models.OneToOneField(Enrollment, on_delete=models.CASCADE, related_name='invoice', verbose_name='Inscription')
    amount = models.DecimalField('Montant (€)', max_digits=8, decimal_places=2, default=0)
    status = models.CharField('Statut', max_length=16, choices=Status.choices, default=Status.UNPAID)
    issued_on = models.DateTimeField('Émise le', default=timezone.now)
    paid_on = models.DateTimeField('Payée le', null=True, blank=True)
    # Remote provider identifier (Lingo)
    lingo_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ['-issued_on']

    def __str__(self):
        return f"Facture #{self.pk} - {self.enrollment} - {self.amount}€ ({self.status})"
