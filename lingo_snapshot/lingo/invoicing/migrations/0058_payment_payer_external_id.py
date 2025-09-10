from django.db import migrations


def forward(apps, schema_editor):
    Payment = apps.get_model('invoicing', 'Payment')
    for payment in Payment.objects.all():
        for invoice_payment in payment.invoicepayment_set.all():
            payment.payer_external_id = invoice_payment.invoice.payer_external_id
            payment.payer_first_name = invoice_payment.invoice.payer_first_name
            payment.payer_last_name = invoice_payment.invoice.payer_last_name
            payment.save()
            break


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0057_payment_payer_external_id'),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=migrations.RunPython.noop),
    ]
