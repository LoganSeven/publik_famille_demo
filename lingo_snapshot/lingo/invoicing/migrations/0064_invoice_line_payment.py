from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0063_invoice_line_payment'),
    ]

    operations = [
        migrations.DeleteModel(
            name='InvoicePayment',
        ),
    ]
