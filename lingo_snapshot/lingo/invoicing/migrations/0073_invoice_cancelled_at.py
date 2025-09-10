from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0072_payment_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='cancelled_at',
            field=models.DateTimeField(null=True),
        ),
    ]
