# documents/models.py
"""
Database models for the documents application.

This module defines models for handling user documents,
including invoices stored as PDF files.
"""

from django.db import models
from django.conf import settings
from billing.models import Invoice


class DocumentKind(models.TextChoices):
    """
    Enumeration of document types.

    Attributes
    ----------
    FACTURE : str
        Represents an invoice document (verbose name: "Facture").
    """

    FACTURE = "FACTURE", "Facture"


class Document(models.Model):
    """
    Model representing a stored document.

    Attributes
    ----------
    user : ForeignKey
        The user who owns the document.
    kind : CharField
        The type of document (e.g., FACTURE).
    title : CharField
        The human-readable title of the document.
    file : FileField
        The uploaded file path for the document.
    created_at : DateTimeField
        Timestamp when the document was created.
    invoice : OneToOneField
        Optional link to an Invoice when the document
        represents a billing invoice.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Utilisateur",
    )
    kind = models.CharField("Type", max_length=32, choices=DocumentKind.choices)
    title = models.CharField("Titre", max_length=255)
    file = models.FileField("Fichier", upload_to="documents/%Y/%m/%d/")
    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    invoice = models.OneToOneField(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document",
        verbose_name="Facture liée",
    )

    class Meta:
        """
        Metadata for the Document model.

        Attributes
        ----------
        ordering : list
            Default ordering by descending creation date.
        """

        ordering = ["-created_at"]

    def __str__(self) -> str:
        """
        Return a string representation of the document.

        Returns
        -------
        str
            The document title.
        """
        return self.title
