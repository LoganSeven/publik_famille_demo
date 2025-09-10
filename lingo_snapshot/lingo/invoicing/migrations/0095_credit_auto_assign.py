from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0094_credit_usable'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='assign_credits_on_creation',
            field=models.BooleanField(
                default=True, verbose_name='Use a credit when created to pay old invoices'
            ),
        ),
    ]
