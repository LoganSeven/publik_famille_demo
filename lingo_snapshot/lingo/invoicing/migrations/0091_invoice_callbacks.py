from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0090_payment_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoice',
            name='cancel_callback_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='cancel_callback_url',
            field=models.URLField(blank=True),
        ),
    ]
