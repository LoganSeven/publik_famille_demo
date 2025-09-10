from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0112_date_refund'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='date_payment',
            field=models.DateField(null=True, verbose_name='Payment date'),
        ),
    ]
