import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0030_invoice_order_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='draftinvoice',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
        migrations.AlterField(
            model_name='draftinvoiceline',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='pool',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.pool'
            ),
        ),
    ]
