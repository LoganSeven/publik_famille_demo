# billing/migrations/0002_invoice_lingo_id.py
"""
Migration to extend the Invoice model with Lingo integration.

This migration adds the ``lingo_id`` field to the Invoice model,
which stores an optional identifier used by the remote Lingo
billing backend.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Migration class for extending the Invoice model.

    Attributes
    ----------
    dependencies : list
        Declares a dependency on the initial migration of the
        billing app, ensuring the Invoice model exists first.
    operations : list
        Adds the ``lingo_id`` field to the Invoice model.
    """

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="lingo_id",
            field=models.CharField(
                max_length=64,
                null=True,
                blank=True,
            ),
        ),
    ]
