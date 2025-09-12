# billing/migrations/0001_initial.py
"""
Initial migration for the billing application.

This migration creates the Invoice model, which is linked
one-to-one with an Enrollment and stores billing data such
as amount, status, and issue/payment dates.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Initial migration class for the billing application.

    Attributes
    ----------
    initial : bool
        Marks this migration as the first for the app.
    dependencies : list
        Declares a dependency on the initial migration of
        the activities app to ensure Enrollment is created first.
    operations : list
        Creates the Invoice model with its fields and metadata.
    """

    initial = True

    dependencies = [
        # Depends on activities app because Invoice links to Enrollment
        ("activities", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Invoice",
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
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=8,
                        verbose_name="Montant (€)",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("UNPAID", "Non payée"), ("PAID", "Payée")],
                        default="UNPAID",
                        max_length=16,
                        verbose_name="Statut",
                    ),
                ),
                (
                    "issued_on",
                    models.DateTimeField(verbose_name="Émise le"),
                ),
                (
                    "paid_on",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Payée le",
                    ),
                ),
                (
                    "enrollment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invoice",
                        to="activities.enrollment",
                        verbose_name="Inscription",
                    ),
                ),
            ],
            options={"ordering": ["-issued_on"]},
        ),
    ]
