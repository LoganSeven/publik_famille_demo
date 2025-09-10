from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0056_payment_uuid'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='payer_external_id',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_first_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payment',
            name='payer_last_name',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
