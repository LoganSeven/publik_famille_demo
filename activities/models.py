# activities/models.py
from django.db import models
from django.utils import timezone
from families.models import Child

class Activity(models.Model):
    title = models.CharField('Titre', max_length=200)
    description = models.TextField('Description', blank=True)
    fee = models.DecimalField('Tarif (€)', max_digits=8, decimal_places=2, default=0)
    start_date = models.DateField('Date de début', null=True, blank=True)
    end_date = models.DateField('Date de fin', null=True, blank=True)
    capacity = models.PositiveIntegerField('Capacité', null=True, blank=True)
    is_active = models.BooleanField('Active', default=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

class Enrollment(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = 'PENDING_PAYMENT', 'En attente de paiement'
        CONFIRMED = 'CONFIRMED', 'Confirmée'
        CANCELLED = 'CANCELLED', 'Annulée'

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='enrollments', verbose_name='Enfant')
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='enrollments', verbose_name='Activité')
    status = models.CharField('Statut', max_length=32, choices=Status.choices, default=Status.PENDING_PAYMENT)
    requested_on = models.DateTimeField('Demandée le', default=timezone.now)
    approved_on = models.DateTimeField('Approuvée le', null=True, blank=True)

    class Meta:
        unique_together = ('child', 'activity')
        ordering = ['-requested_on']

    def __str__(self):
        return f"{self.child} -> {self.activity} ({self.status})"
