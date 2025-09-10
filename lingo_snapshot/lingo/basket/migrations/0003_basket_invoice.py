import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0072_payment_info'),
        ('basket', '0002_basket'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='invoice',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.invoice'
            ),
        ),
    ]
