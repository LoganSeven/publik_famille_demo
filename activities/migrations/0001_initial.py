# activities/migrations/0001_initial.py
"""
Initial migration for the activities application.

This migration creates the core models for the activities app:
- Activity: represents an offered activity.
- Enrollment: represents a child's enrollment in an activity.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Initial migration class for the activities application.

    Attributes
    ----------
    initial : bool
        Indicates that this is the first migration for the app.
    dependencies : list
        Lists dependencies on other apps' migrations, here the
        families app.
    operations : list
        Contains the creation of Activity and Enrollment models,
        including metadata and relationships.
    """

    initial = True

    dependencies = [
        # Depends on the initial migration of the families app
        ("families", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Activity",
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
                    "title",
                    models.CharField(max_length=200, verbose_name="Titre"),
                ),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Description"),
                ),
                (
                    "fee",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=8,
                        verbose_name="Tarif (€)",
                    ),
                ),
                (
                    "start_date",
                    models.DateField(
                        blank=True, null=True, verbose_name="Date de début"
                    ),
                ),
                (
                    "end_date",
                    models.DateField(
                        blank=True, null=True, verbose_name="Date de fin"
                    ),
                ),
                (
                    "capacity",
                    models.PositiveIntegerField(
                        blank=True, null=True, verbose_name="Capacité"
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Active"),
                ),
            ],
            options={"ordering": ["title"]},
        ),
        migrations.CreateModel(
            name="Enrollment",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING_PAYMENT", "En attente de paiement"),
                            ("CONFIRMED", "Confirmée"),
                            ("CANCELLED", "Annulée"),
                        ],
                        default="PENDING_PAYMENT",
                        max_length=32,
                        verbose_name="Statut",
                    ),
                ),
                (
                    "requested_on",
                    models.DateTimeField(verbose_name="Demandée le"),
                ),
                (
                    "approved_on",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Approuvée le"
                    ),
                ),
                # Foreign key to Activity
                (
                    "activity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enrollments",
                        to="activities.activity",
                        verbose_name="Activité",
                    ),
                ),
                # Foreign key to Child
                (
                    "child",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="enrollments",
                        to="families.child",
                        verbose_name="Enfant",
                    ),
                ),
            ],
            options={"ordering": ["-requested_on"]},
        ),
        migrations.AlterUniqueTogether(
            name="enrollment",
            unique_together={("child", "activity")},
        ),
    ]
