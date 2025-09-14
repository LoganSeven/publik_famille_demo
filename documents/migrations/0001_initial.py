# documents/migrations/0001_initial.py
"""
Initial migration for the documents application.

This migration creates the Document model, which stores
uploaded documents such as invoices and associates them
with users and optionally invoices.
"""

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    """
    Initial migration class for the documents application.

    Attributes
    ----------
    initial : bool
        Marks this migration as the first for the app.
    dependencies : list
        Declares dependencies on the user model and billing
        app migrations, since Document is linked to both.
    operations : list
        Creates the Document model with its fields, relations,
        and metadata.
    """

    initial = True

    dependencies = [
        # Ensure user model exists before linking documents to it
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # Ensure Invoice exists before linking optional invoice documents
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[("FACTURE", "Facture")],
                        max_length=32,
                        verbose_name="Type",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        max_length=255,
                        verbose_name="Titre",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        upload_to="documents/%Y/%m/%d/",
                        verbose_name="Fichier",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Créé le",
                    ),
                ),
                (
                    "invoice",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document",
                        to="billing.invoice",
                        verbose_name="Facture liée",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Utilisateur",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
