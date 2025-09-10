from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0081_invoice_payment_callback'),
    ]

    operations = [
        migrations.AddField(
            model_name='regie',
            name='invoice_main_colour',
            field=models.CharField(default='#DF5A13', max_length=7, verbose_name='Main colour in invoice'),
        ),
    ]
