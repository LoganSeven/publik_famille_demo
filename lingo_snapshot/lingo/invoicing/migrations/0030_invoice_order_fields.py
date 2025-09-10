from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0029_invoice_uuid'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='order_date',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='order_id',
            field=models.CharField(max_length=200, null=True),
        ),
    ]
