import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0074_credit'),
        ('basket', '0005_basket_expiry'),
    ]

    operations = [
        migrations.AddField(
            model_name='basket',
            name='credit',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to='invoicing.credit'
            ),
        ),
    ]
