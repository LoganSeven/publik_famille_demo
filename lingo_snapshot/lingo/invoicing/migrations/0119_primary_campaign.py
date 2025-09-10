import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0118_collection_pay_invoices'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='primary_campaign',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='invoicing.campaign',
                related_name='corrective_campaigns',
            ),
        ),
    ]
