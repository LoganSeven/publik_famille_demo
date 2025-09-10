from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0111_date_invoicing'),
    ]

    operations = [
        migrations.AddField(
            model_name='refund',
            name='date_refund',
            field=models.DateField(null=True, verbose_name='Refund date'),
        ),
    ]
