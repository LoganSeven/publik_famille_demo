# activities/migrations/0002_enrollment_wcs_id.py
"""
Migration to extend the Enrollment model.

This migration adds the ``wcs_id`` field to the Enrollment model.
The field stores an optional identifier for synchronizing with
a remote WCS (Web Citizen Service) backend.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Migration class for extending the Enrollment model.

    Attributes
    ----------
    dependencies : list
        References the migration that updated the
        ``requested_on`` field. Update this dependency if
        the actual latest migration differs.
    operations : list
        Adds the new ``wcs_id`` field to the Enrollment model.
    """

    dependencies = [
        ("activities", "0002_alter_enrollment_requested_on"),
    ]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="wcs_id",
            field=models.CharField(
                max_length=64,
                null=True,
                blank=True,
            ),
        ),
    ]
