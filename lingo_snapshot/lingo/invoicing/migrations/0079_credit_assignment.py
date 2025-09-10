import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0078_credit_assignment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='creditassignment',
            name='invoice',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoice'),
        ),
        migrations.AlterField(
            model_name='creditassignment',
            name='payment',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.payment'
            ),
        ),
    ]
