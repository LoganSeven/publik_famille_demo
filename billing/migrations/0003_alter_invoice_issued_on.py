# billing/migrations/0003_alter_invoice_issued_on.py
"""
Migration to update the default value of the Invoice.issued_on field.

This migration sets the default of the ``issued_on`` field to
``django.utils.timezone.now`` to ensure that new invoices are
automatically timestamped with the current date and time.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Migration class for updating the Invoice model.

    Attributes
    ----------
    dependencies : list
        Declares a dependency on the previous migration
        that introduced the ``lingo_id`` field.
    operations : list
        Alters the ``issued_on`` field to use
        ``timezone.now`` as its default value.
    """

    dependencies = [
        ("billing", "0002_invoice_lingo_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="invoice",
            name="issued_on",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                verbose_name="Émise le",
            ),
        ),
    ]
