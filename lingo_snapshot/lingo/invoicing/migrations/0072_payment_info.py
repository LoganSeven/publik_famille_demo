from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0071_regie_cashier_city_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='payment_info',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
