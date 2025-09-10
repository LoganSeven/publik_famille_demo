from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0080_refund'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoice',
            name='payment_callback_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='payment_callback_url',
            field=models.URLField(blank=True),
        ),
    ]
