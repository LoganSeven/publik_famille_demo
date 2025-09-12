# activities/migrations/0002_alter_enrollment_requested_on.py
"""
Migration to update the Enrollment model.

This migration alters the ``requested_on`` field of the
Enrollment model to use the default value
``django.utils.timezone.now``.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Migration class for altering the Enrollment model.

    Attributes
    ----------
    dependencies : list
        References the initial migration of the activities app.
    operations : list
        Alters the requested_on field to set its default value
        to ``timezone.now``.
    """

    dependencies = [
        ("activities", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="enrollment",
            name="requested_on",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                verbose_name="Demandée le",
            ),
        ),
    ]
