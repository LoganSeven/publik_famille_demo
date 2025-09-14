# families/migrations/0001_initial.py
"""
Initial migration for the families application.

This migration creates the Child model, which represents
a child belonging to a user (parent).
"""

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    """
    Initial migration class for the families application.

    Attributes
    ----------
    initial : bool
        Marks this migration as the first for the app.
    dependencies : list
        Declares a dependency on the swappable user model
        to support custom AUTH_USER_MODEL.
    operations : list
        Creates the Child model with its fields and metadata.
    """

    initial = True

    dependencies = [
        # Ensures the user model exists before linking Child to it
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Child",
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
                    "first_name",
                    models.CharField(
                        max_length=100,
                        verbose_name="Prénom",
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        max_length=100,
                        verbose_name="Nom",
                    ),
                ),
                (
                    "birth_date",
                    models.DateField(verbose_name="Date de naissance"),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["last_name", "first_name"]},
        ),
    ]
