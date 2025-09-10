import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0039_payment_type'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='payment',
            name='payment_type',
        ),
        migrations.AlterField(
            model_name='payment',
            name='new_payment_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='invoicing.paymenttype'),
        ),
    ]
