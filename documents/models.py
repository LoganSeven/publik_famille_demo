# documents/models.py
from django.db import models
from django.conf import settings
from billing.models import Invoice

class DocumentKind(models.TextChoices):
    FACTURE = 'FACTURE', 'Facture'

class Document(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='documents', verbose_name='Utilisateur')
    kind = models.CharField('Type', max_length=32, choices=DocumentKind.choices)
    title = models.CharField('Titre', max_length=255)
    file = models.FileField('Fichier', upload_to='documents/%Y/%m/%d/')
    created_at = models.DateTimeField('Créé le', auto_now_add=True)
    invoice = models.OneToOneField(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name='document', verbose_name='Facture liée')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title
