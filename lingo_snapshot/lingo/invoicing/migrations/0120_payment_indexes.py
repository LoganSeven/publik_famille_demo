from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0119_primary_campaign'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(models.F('order_id'), name='payment_order_id_idx'),
        ),
    ]
