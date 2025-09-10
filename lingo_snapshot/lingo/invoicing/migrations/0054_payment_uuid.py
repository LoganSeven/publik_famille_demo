from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0053_payment_counter'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='uuid',
            field=models.UUIDField(editable=False, null=True),
        ),
    ]
