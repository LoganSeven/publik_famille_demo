# accounts/migrations/0001_initial.py
"""
Initial migration for the accounts application.

This migration creates the UserProfile model, which extends
the built-in Django user model with additional attributes,
including an identity verification flag.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Migration class for initializing the accounts application.

    Attributes
    ----------
    initial : bool
        Indicates that this is the first migration of the app.
    dependencies : list
        Specifies dependencies, including the swappable user model.
    operations : list
        Defines the creation of the UserProfile model with fields
        and relationships.
    """

    initial = True

    dependencies = [
        # Dependency on the user model to support custom AUTH_USER_MODEL
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
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
                # Identity verification flag
                ("id_verified", models.BooleanField(default=False)),
                # One-to-one relationship with the user model
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
